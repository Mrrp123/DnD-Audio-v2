#ifdef GL_ES
precision highp float;
#endif

/* Outputs from the vertex shader */
varying vec4 frag_color;
varying vec2 tex_coord0;

/* uniform texture samplers */
uniform sampler2D texture0;

uniform float left_edge;
uniform float right_edge;

vec4 effect(vec4 color, vec2 tex_coords)
{
    color.a *= -smoothstep(left_edge, right_edge, tex_coords.x) + 1.0;
    return color;
}

void main(void) 
{
    vec4 normal_color = frag_color * texture2D(texture0, tex_coord0);
    vec4 effect_color = effect(normal_color, tex_coord0);
    gl_FragColor = effect_color;
}