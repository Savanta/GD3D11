// Tessellation for world meshes. See docs/tessellation.md for why this file had to be written:
// the PNAEN pair that ships with the tree targets the mesh editor's preview vertex shader, so it
// cannot be bound to world geometry.
//
// Both stages live here: the loader compiles HSMain as hs_5_0 and DSMain as ds_5_0 from one path,
// the same way OceanTess.hlsl is wired.
//
// VS_OUT mirrors VS_ExInstancedObj.hlsl's output exactly. That match is the whole point - a hull
// shader whose input signature differs from the vertex shader in the slot is a device error, not a
// visual glitch.

cbuffer TessParams : register(b0)
{
    float4x4 Proj;      // stored pre-transposed by the engine: multiply row-vector style
    float    Factor;    // tessellation factor, edges and interior alike
    float    DispScale; // PN displacement scale, in view-space units
    float2   _tessPad;
};

struct VS_OUT
{
    float4 pos      : SV_Position;
    float2 uv       : TEXCOORD0;
    float2 uv2      : TEXCOORD1;
    float4 diffuse  : TEXCOORD2;
    float4 tangent  : TEXCOORD3;
    float3 normalVS : TEXCOORD4;
    float3 viewPos  : TEXCOORD5;
    float4 clip     : TEXCOORD6;
    float4 prevClip : TEXCOORD7;
};

struct HS_CONSTANT
{
    float edges[3] : SV_TessFactor;
    float inside   : SV_InsideTessFactor;
};

// A uniform factor cannot crack between patches: neighbours always share it. Distance-adaptive
// factors need per-edge values from both endpoints (AdaptiveTesselation.h has them) - that is the
// next step, not this one.
HS_CONSTANT HSConstant( InputPatch<VS_OUT, 3> patch )
{
    HS_CONSTANT o;
    o.edges[0] = Factor;
    o.edges[1] = Factor;
    o.edges[2] = Factor;
    o.inside   = Factor;
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

    float3 v = p[0].viewPos * bary.x + p[1].viewPos * bary.y + p[2].viewPos * bary.z;
    float3 n = normalize( p[0].normalVS * bary.x + p[1].normalVS * bary.y + p[2].normalVS * bary.z );

    // PN-triangle: push the subdivided surface along the interpolated normal by the barycentric
    // deviation of the control points. Without this term subdividing a flat triangle only costs
    // triangles and changes nothing visible - the silhouettes stay exactly as angular.
    float w = 2.0 * bary.x * bary.y * bary.z;

    o.normalVS = n;
    o.viewPos  = v + n * ( w * DispScale );
    o.pos      = mul( float4( o.viewPos, 1.0 ), Proj );
    return o;
}
