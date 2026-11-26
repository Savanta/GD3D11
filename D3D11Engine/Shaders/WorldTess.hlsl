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
    matrix Proj;       // view -> clip; same convention as VS_Ex's M_ViewProj (mul(vector, matrix))
    float  Factor;     // tessellation factor of every edge and of the interior, 1..64
    float  DispScale;  // displacement as a fraction of the patch's mean edge length
    float2 TessPad;
};

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

struct HS_CONSTANT
{
    float edges[3] : SV_TessFactor;
    float inside   : SV_InsideTessFactor;
};

// A uniform factor cannot crack between patches: neighbours always share it. Distance-adaptive
// factors need per-edge values from both endpoints (AdaptiveTesselation.h has them) - next step.
HS_CONSTANT HSConstant( InputPatch<VS_OUT, 3> patch )
{
    const float f = clamp( Factor, 1.0f, 64.0f );
    HS_CONSTANT o;
    o.edges[0] = f;
    o.edges[1] = f;
    o.edges[2] = f;
    o.inside   = f;
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

    // Texture relief: the normal map's tangent-space slope stands in for a height field, so the
    // surface follows the grain of the texture. 1 - n.z is 0 for a flat texel and grows where the
    // map tilts the surface; the half weight keeps it a detail term under the silhouette bump.
    const float3 nTile = TX_Relief.SampleLevel( SS_Linear, o.uv, 0 ).xyz * 2.0f - 1.0f;
    const float relief = saturate( 1.0f - nTile.z ) * 0.5f;

    o.normalVS = n;
    o.viewPos  = v + n * ( ( w + relief ) * DispScale * edgeMean );
    o.pos      = mul( float4( o.viewPos, 1.0f ), Proj );

    return o;
}
