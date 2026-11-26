// Tessellation of the static world mesh. See docs/tessellation.md.
//
// Both stages live here: the loader compiles HSMain as hs_5_0 and DSMain as ds_5_0 from one path,
// the same way OceanTess.hlsl is wired.
//
// VS_OUT mirrors VS_ExPacked.hlsl's VS_OUTPUT field for field. That match is the whole point - the
// visible world is drawn by VS_ExPacked from the packed 36-byte stream (D3D11GraphicsEngine::
// DrawWorldMesh), and a hull shader whose input signature differs from the vertex shader in the
// slot is a device error, not a visual glitch.
//
// Both stages read TessParams at b0, so the engine binds that buffer to the hull AND the domain
// stage (D3D11HDShader::UpdateBuffer only reaches the domain shader).

cbuffer TessParams : register( b0 )
{
    matrix Proj;         // view -> clip; same convention as VS_Ex's M_ViewProj (mul(vector, matrix))
    float  Factor;       // ceiling for the tessellation factor of any edge, 1..64
    float  DispScale;    // displacement as a fraction of the patch's mean edge length
    float2 ScreenSize;   // render target size in pixels, for the screen-space edge measurement
    float  TargetPixels; // one tessellated edge should be about this long on screen
    float  MinFactor;    // never go below this, so a small patch still gets a real patch
    float  ReliefMode;   // 0 = none, 1 = normal-map slope, 2 = height map, 3 = diffuse luminance
    float  TessPad;
    float3 UpVS;         // world up in view space: dot(normalVS, UpVS) is the world Y of the normal
    float  TessPad2;
};

#include "AdaptiveTesselation.h"

// Material normal map, bound to the domain stage by the world draw. Sampling it per domain vertex
// is what makes the displaced surface follow the texture (brick rows, rock grain) instead of only
// the barycentric bump. A material without a normal map gets the engine's white texture, which
// decodes to a flat normal and therefore to exactly zero relief.
Texture2D    TX_Relief : register( t0 );
SamplerState SS_Linear : register( s0 );

struct VS_OUT
{
    float2 uv       : TEXCOORD0;
    float2 uv2      : TEXCOORD1;
    float4 diffuse  : TEXCOORD2;
    float3 normalVS : TEXCOORD4;
    float3 viewPos  : TEXCOORD5;
    float4 clip     : TEXCOORD6;
    float4 prevClip : TEXCOORD7;
    float4 tangent  : TEXCOORD3;
    float4 pos      : SV_Position;
    float4 farField : TEXCOORD8;
};

// Luminance of the relief texture at a UV, used by the diffuse mode.
float WorldTessLuminance( float2 uv )
{
    return dot( TX_Relief.SampleLevel( SS_Linear, uv, 0 ).rgb, float3( 0.2126f, 0.7152f, 0.0722f ) );
}

struct HS_CONSTANT
{
    float edges[3] : SV_TessFactor;
    float inside   : SV_InsideTessFactor;
};

// A uniform factor cannot crack between patches, but it also multiplies every triangle in the world
// by the same amount - including the ones a few pixels wide at the horizon. These give each edge a
// factor from its own on-screen length instead: the split stops as soon as an edge is about
// TargetPixels long, and the ceiling is Factor. Symmetric in both endpoints, so neighbours agree.
float2 WorldTessScreenPos( float3 viewPos )
{
    const float4 clip = mul( float4( viewPos, 1.0f ), Proj );
    return ( clip.xy / max( abs( clip.w ), 1e-4f ) * 0.5f + 0.5f ) * ScreenSize;
}

float WorldTessEdgeFactor( float3 a, float3 b )
{
    // AMD's helper (Shaders/AdaptiveTesselation.h) returns a 0..1 scale for the edge, with 1 at the
    // maximum factor. Mapped onto [MinFactor, Factor] so a small edge still gets a real patch
    // instead of being culled by a zero factor.
    const float scale = GetScreenSpaceAdaptiveScaleFactor(
        WorldTessScreenPos( a ), WorldTessScreenPos( b ), max( Factor, MinFactor ), max( TargetPixels, 1.0f ) );
    return MinFactor + scale * ( max( Factor, MinFactor ) - MinFactor );
}

HS_CONSTANT HSConstant( InputPatch<VS_OUT, 3> p )
{
    HS_CONSTANT o;
    o.edges[0] = WorldTessEdgeFactor( p[1].viewPos, p[2].viewPos );
    o.edges[1] = WorldTessEdgeFactor( p[2].viewPos, p[0].viewPos );
    o.edges[2] = WorldTessEdgeFactor( p[0].viewPos, p[1].viewPos );
    o.inside   = max( o.edges[0], max( o.edges[1], o.edges[2] ) );
    return o;
}

[domain("tri")]
[partitioning("fractional_odd")]
[outputtopology("triangle_cw")]
[outputcontrolpoints(3)]
[patchconstantfunc("HSConstant")]
VS_OUT HSMain( InputPatch<VS_OUT, 3> patch, uint i : SV_OutputControlPointID )
{
    return patch[i];
}

[domain("tri")]
VS_OUT DSMain( HS_CONSTANT c, float3 bary : SV_DomainLocation, const OutputPatch<VS_OUT, 3> p )
{
    VS_OUT o;
    o.uv       = p[0].uv * bary.x + p[1].uv * bary.y + p[2].uv * bary.z;
    o.uv2      = p[0].uv2 * bary.x + p[1].uv2 * bary.y + p[2].uv2 * bary.z;
    o.diffuse  = p[0].diffuse * bary.x + p[1].diffuse * bary.y + p[2].diffuse * bary.z;
    o.tangent  = p[0].tangent * bary.x + p[1].tangent * bary.y + p[2].tangent * bary.z;
    o.clip     = p[0].clip * bary.x + p[1].clip * bary.y + p[2].clip * bary.z;
    o.prevClip = p[0].prevClip * bary.x + p[1].prevClip * bary.y + p[2].prevClip * bary.z;
    o.farField = p[0].farField * bary.x + p[1].farField * bary.y + p[2].farField * bary.z;

    float3 v = p[0].viewPos * bary.x + p[1].viewPos * bary.y + p[2].viewPos * bary.z;
    float3 n = normalize( p[0].normalVS * bary.x + p[1].normalVS * bary.y + p[2].normalVS * bary.z );

    // PN-triangle: push the subdivided surface along the interpolated normal by the barycentric
    // deviation of the control points. 2xyz peaks at 2/27 on the centroid and is 0 on every edge,
    // which is what keeps neighbouring patches watertight. Subdividing a flat triangle displaced by
    // that term alone is invisible, so the amplitude is scaled by the patch's own mean edge length:
    // DispScale is a fraction of the triangle size, so the default 0.12 stays a subtle bulge instead
    // of a fixed number of units that is invisible up close and a spike on a large wall.
    const float w = 2.0f * bary.x * bary.y * bary.z;
    const float edgeMean = ( length( p[1].viewPos - p[0].viewPos )
                           + length( p[2].viewPos - p[1].viewPos )
                           + length( p[0].viewPos - p[2].viewPos ) ) / 3.0f;

    // Texture relief, in the mode the draw picked for this material:
    //   3 - the material's own diffuse, luminance as height. Always available (every world material
    //       has a diffuse), good for brick, stone, wood and metal grime, wrong wherever brightness is
    //       not height - painted highlights, baked shadows, decals. This is what makes the feature
    //       work in this mod, where no world texture ever carries an extra map.
    //   2 - '<tex>_HEIGHT.DDS': 0 is the base surface, 1 the peak, so the displacement is the height.
    //   1 - the normal map's tangent-space slope (1 - n.z, half weight): a proxy, not a height.
    //   0 - white texture, nothing displaced.
    const float4 reliefTexel = TX_Relief.SampleLevel( SS_Linear, o.uv, 0 );
    float relief = 0.0f;
    if ( ReliefMode > 2.5f ) {
        // High-pass, not level: a bright but *flat* stretch (snow, frost, a painted highlight) has a
        // large luminance and would otherwise be pushed out as one solid spike - measured, they read
        // as white icicles hanging off the rock. Only variation inside the texture is height, so the
        // Laplacian against the four neighbouring texels is what displaces, clamped and weighted down
        // because a diffuse is a proxy of a proxy.
        uint tw, th;
        TX_Relief.GetDimensions( tw, th );
        const float2 texel = 1.0f / float2( max( (float)tw, 1.0f ), max( (float)th, 1.0f ) );
        const float c = WorldTessLuminance( o.uv );
        const float lateral = 0.25f * ( WorldTessLuminance( o.uv + float2( texel.x, 0.0f ) )
                                      + WorldTessLuminance( o.uv - float2( texel.x, 0.0f ) )
                                      + WorldTessLuminance( o.uv + float2( 0.0f, texel.y ) )
                                      + WorldTessLuminance( o.uv - float2( 0.0f, texel.y ) ) );
        // 0.08 of the triangle edge at DispScale 1: about a centimetre-scale relief on a 1 m
        // triangle. Anything larger turned texture edges into cones (measured at 1.0 with 0.35).
        relief = saturate( ( c - lateral ) * 4.0f ) * 0.08f;
    } else if ( ReliefMode > 1.5f ) {
        // 0.5 is the neutral surface, so a flat stretch of the map displaces nothing and the range
        // is symmetric around the original geometry.
        relief = ( reliefTexel.r - 0.5f ) * 2.0f;
    } else if ( ReliefMode > 0.5f ) {
        const float3 nTile = reliefTexel.xyz * 2.0f - 1.0f;
        relief = saturate( 1.0f - nTile.z ) * 0.5f;
    }

    // Walkable surfaces keep their collision shape. A floor's collision polygon is the flat triangle,
    // so bulging it up leaves the hero standing inside the rock (measured at displacement 8) - the
    // ground is the one place where a mismatch is felt during play rather than seen. Fade the
    // displacement out as the surface turns from vertical (walls, rocks) towards horizontal.
    // 0.5 is 60 degrees off vertical (a cliff, full displacement), 0.85 is about 32 degrees (gentle
    // ground, none). Between them it fades, so the seam between a wall and the ground it meets is a
    // gradient, not a ledge.
    const float upness = abs( dot( n, UpVS ) );
    const float walkable = 1.0f - smoothstep( 0.5f, 0.85f, upness );

    o.normalVS = n;
    o.viewPos  = v + n * ( ( w + relief ) * walkable * DispScale * edgeMean );
    o.pos      = mul( float4( o.viewPos, 1.0f ), Proj );

    return o;
}
