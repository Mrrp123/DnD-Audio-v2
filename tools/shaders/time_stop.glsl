#ifdef GL_ES
precision highp float;
#endif

/* Outputs from the vertex shader */
varying vec4 frag_color;
varying vec2 tex_coord0;

/* uniform texture samplers */
uniform sampler2D texture0;
uniform vec2 resolution;
uniform float time;
uniform float t0;

const vec3  angles       = vec3(0.5235987756, 2.617993878, 4.7123889804);

const vec4  kRGBToYPrime = vec4 (0.299, 0.587, 0.114, 0.0);
const vec4  kRGBToI      = vec4 (0.596, -0.275, -0.321, 0.0);
const vec4  kRGBToQ      = vec4 (0.212, -0.523, 0.311, 0.0);

const vec4  kYIQToR      = vec4 (1.0, 0.956, 0.621, 0.0);
const vec4  kYIQToG      = vec4 (1.0, -0.272, -0.647, 0.0);
const vec4  kYIQToB      = vec4 (1.0, -1.107, 1.704, 0.0);

vec2 barrel_distort(vec2 p, float barrel_power)
{
    vec2 p_prime = vec2(p.x, p.y * resolution.y/resolution.x);

    float theta  = atan(p_prime.y, p_prime.x);
    float radius = length(p_prime);
    radius = pow(radius, barrel_power);
    p.x = radius * cos(theta);
    p.y = radius * sin(theta) * resolution.x/resolution.y;
    return 0.5 * (p + 1.0);
}

vec4 desaturate(vec4 color, float saturation)
{
    float greyscale = dot(color.rgb, vec3(0.2126, 0.7152, 0.0722));
    vec3 desaturated_color = mix(vec3(greyscale, greyscale, greyscale), color.rgb, saturation);
    return vec4(desaturated_color.rgb, color.a);
}  

vec4 hue_shift(vec4 color)
{
    // First invert color
    color = vec4(0.8 - color.xyz, color.w);

    // Convert to YIQ
    float   YPrime  = dot (color, kRGBToYPrime);
    float   I      = dot (color, kRGBToI);
    float   Q      = dot (color, kRGBToQ);

    // Calculate the hue and chroma
    float   hue     = atan (Q, I);
    float   chroma  = sqrt (I * I + Q * Q);

    // Make the user's adjustments
    hue += time * 3.0;

    // Convert back to YIQ
    Q = chroma * sin (hue);
    I = chroma * cos (hue);

    // Convert back to RGB
    vec4    yIQ   = vec4 (YPrime, I, Q, 0.0);
    color.r = dot (yIQ, kYIQToR);
    color.g = dot (yIQ, kYIQToG);
    color.b = dot (yIQ, kYIQToB);

    return color;
}

vec4 color_offset(vec4 color, sampler2D texture, vec2 tex_coords, float dist_offset)
{


    vec3 x_diff = -dist_offset * cos(angles) * (resolution.y / resolution.x); // normalize to yaxis
    vec3 y_diff = -dist_offset * sin(angles);

    vec4 red   = texture2D(texture, vec2(tex_coords.x + x_diff.r, tex_coords.y + y_diff.r));
    vec4 green = texture2D(texture, vec2(tex_coords.x + x_diff.g, tex_coords.y + y_diff.g));
    vec4 blue  = texture2D(texture, vec2(tex_coords.x + x_diff.b, tex_coords.y + y_diff.b));

    return vec4(red.r, green.g, blue.b, color.w);

}

vec4 effect(vec4 color, sampler2D texture, vec2 tex_coords, vec2 coords)
{
    float t = (time - t0);


    if (t0 > 0.0) // Special value for t0, if t0 < 0, don't do anything yet
    {
        if (t < 0.9375)
        {
            // if we're within some (circular) distance from the center, apply various effects
            float circle_size = length(resolution) * (t*3.0);
            if (distance(resolution / 2.0, coords) < circle_size)
            {
                // apply barrel distortion
                float t_prime = t - 0.9375;
                float barrel_power = -1.1377777777777 * (t_prime*t_prime) + 2.0;
                tex_coords = barrel_distort((tex_coords * 2.0) - 1.0, barrel_power);

                // apply color split effect
                float dist_offset = -0.0147289687791 * (t_prime*t_prime*t_prime*t_prime*t_prime*t_prime) + 0.01;
                color = color_offset(color, texture, tex_coords, dist_offset);

                // apply the hue shift to our color
                color = hue_shift(color);

            }
            // if we are outside the circle, apply inward barrel effect
            else 
            {
                float barrel_power = -25.0 * (t*t) + 1.0;
                tex_coords = barrel_distort((tex_coords * 2.0) - 1.0, barrel_power);
                color = vec4(texture2D(texture, vec2(tex_coords.x, tex_coords.y)).rgb, color.a);
            }
        }
        // Once the circle has reached a certain size, begin shrinking it
        // Additionally, apply desaturation outside the circle instead of negative barrel distortion
        else if (t <= 1.875)
        {
            float max_circle_size = length(resolution) * 5.625;
            float circle_size = length(resolution) * (-t*3.0) + max_circle_size;

            // apply the same effects inside as before
            if (distance(resolution / 2.0, coords) < circle_size)
            {
                // apply barrel distortion
                float t_prime = t - 0.9375;
                float barrel_power = -1.1377777777777 * (t_prime*t_prime) + 2.0;
                tex_coords = barrel_distort((tex_coords * 2.0) - 1.0, barrel_power);

                // apply color split effect
                float dist_offset = -0.0147289687791 * (t_prime*t_prime*t_prime*t_prime*t_prime*t_prime) + 0.01;
                color = color_offset(color, texture, tex_coords, dist_offset);

                // apply the hue shift to our color
                color = hue_shift(color);
            }
            else // outside we want to apply the desaturation effect only
            {
                color = desaturate(color, 0.2);
            }
        }
        // Simply apply desaturation to the entire texture while we wait for time to resume
        // Why the (unnecessary) accuracy? Fuck you, it's my program, I can do what I want.
        else if (t <= 9.4641723356)
        {
            color = desaturate(color, 0.2);
        }
        // Return saturation back to normal over the course of ~1.7 seconds
        else
        {
            float saturation = clamp((t - 9.464172335600)/1.665, 0.2, 1.0);
            color = desaturate(color, saturation);
        }
    }
    return color;
}

void main(void) 
{
    vec4 normal_color = frag_color * texture2D(texture0, tex_coord0);
    vec4 effect_color = effect(normal_color, texture0, tex_coord0,
                               gl_FragCoord.xy);
    gl_FragColor = effect_color;
}