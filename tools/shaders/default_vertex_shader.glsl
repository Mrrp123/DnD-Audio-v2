// Basic vertex shader to simply pass variables to fragment shader
#ifdef GL_ES
precision highp float;
#endif

/* Outputs to the fragment shader */
varying vec4 frag_color;
varying vec2 tex_coord0;

/* vertex attributes */
attribute vec2     vPosition;
attribute vec2     vTexCoords0;

/* uniform variables */
uniform mat4       modelview_mat;
uniform mat4       projection_mat;
uniform vec4       color;

void main(void) {
    frag_color = color;
    tex_coord0 = vTexCoords0;
    gl_Position = projection_mat * modelview_mat * vec4(vPosition.xy, 0.0, 1.0);
}