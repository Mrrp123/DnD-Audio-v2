from kivy.uix.effectwidget import EffectBase

hue_shift = """

const vec4  kRGBToYPrime = vec4 (0.299, 0.587, 0.114, 0.0);
const vec4  kRGBToI     = vec4 (0.596, -0.275, -0.321, 0.0);
const vec4  kRGBToQ     = vec4 (0.212, -0.523, 0.311, 0.0);

const vec4  kYIQToR   = vec4 (1.0, 0.956, 0.621, 0.0);
const vec4  kYIQToG   = vec4 (1.0, -0.272, -0.647, 0.0);
const vec4  kYIQToB   = vec4 (1.0, -1.107, 1.704, 0.0);

vec4 effect(vec4 color, sampler2D texture, vec2 tex_coords, vec2 coords)
{
    

    // First invert color (somewhat)
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
"""

barrel_distortion = """

uniform float t0;

vec2 distort(vec2 p)
{
    float t = time - t0 - 0.8769;
    float BarrelPower = -1.3 * (t*t) + 2.0;
    float theta  = atan(p.y, p.x);
    float radius = length(p);
    radius = pow(radius, BarrelPower);
    p.x = radius * cos(theta);
    p.y = radius * sin(theta);
    return 0.5 * (p + 1.0);
}

vec4 effect(vec4 color, sampler2D texture, vec2 tex_coords, vec2 coords)
{
    
    tex_coords = distort((tex_coords * 2.0) - 1.0);
    vec4 result = vec4(0.0);

    result += texture2D(texture, vec2(tex_coords.x, tex_coords.y));
    

    return vec4(result.xyz, color.w);
}

"""

anti_barrel_distortion = """

uniform float t0;

vec2 distort(vec2 p)
{
    float t = time - t0;
    float BarrelPower = -100.0 * (t*t) + 1.0;
    float theta  = atan(p.y, p.x);
    float radius = length(p);
    radius = pow(radius, BarrelPower);
    p.x = radius * cos(theta);
    p.y = radius * sin(theta);
    return 0.5 * (p + 1.0);
}

vec4 effect(vec4 color, sampler2D texture, vec2 tex_coords, vec2 coords)
{
    
    tex_coords = distort((tex_coords * 2.0) - 1.0);
    vec4 result = vec4(0.0);

    result += texture2D(texture, vec2(tex_coords.x, tex_coords.y));
    

    return vec4(result.xyz, color.w);
}

"""

color_offset = """
uniform float t0;

vec4 effect(vec4 color, sampler2D texture, vec2 tex_coords, vec2 coords)
{
    vec3 angles = radians(vec3(30, 150, 270));
    float t = time - t0 - 0.8769;
    float dist_offset = -0.022 * (t*t*t*t*t*t) + 0.01;

    vec3 x_diff = -dist_offset * cos(angles);
    vec3 y_diff = -dist_offset * sin(angles);

    vec4 red   = vec4(0.0);
    vec4 green = vec4(0.0);
    vec4 blue  = vec4(0.0);
     
    red   = texture2D(texture, vec2(tex_coords.x + x_diff.r, tex_coords.y + y_diff.r));
    green = texture2D(texture, vec2(tex_coords.x + x_diff.b, tex_coords.y + y_diff.b));
    blue  = texture2D(texture, vec2(tex_coords.x + x_diff.g, tex_coords.y + y_diff.g));

    return vec4(red.r, green.g, blue.b, color.w);

}
"""

desaturation = """
uniform float saturation;

vec4 effect(vec4 color, sampler2D texture, vec2 tex_coords, vec2 coords)
{
    float greyscale = dot(color.rgb, vec3(0.2126, 0.7152, 0.0722));
    vec3 desaturated_color = mix(vec3(greyscale, greyscale, greyscale), color.rgb, saturation);
    return vec4(desaturated_color.rgb, color.a);
}   
"""

time_stop = """
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
    return color;
}
"""


class HueShift(EffectBase):
    '''Inverts the colors in the input.'''
    def __init__(self, *args, **kwargs):
        super(HueShift, self).__init__(*args, **kwargs)
        self.glsl = hue_shift

class BarrelDistortion(EffectBase):
    '''Inverts the colors in the input.'''
    def __init__(self, *args, **kwargs):
        super(BarrelDistortion, self).__init__(*args, **kwargs)
        self.glsl = barrel_distortion

class AntiBarrelDistortion(EffectBase):
    '''Inverts the colors in the input.'''
    def __init__(self, *args, **kwargs):
        super(AntiBarrelDistortion, self).__init__(*args, **kwargs)
        self.glsl = anti_barrel_distortion

class ColorOffset(EffectBase):
    '''Inverts the colors in the input.'''

    def __init__(self, *args, **kwargs):
        super(ColorOffset, self).__init__(*args, **kwargs)
        self.glsl = color_offset

class Desaturate(EffectBase):

    def __init__(self, *args, **kwargs):
        super(Desaturate, self).__init__(*args, **kwargs)
        self.glsl = desaturation

class TimeStop(EffectBase):

    def __init__(self, *args, **kwargs):
        super(TimeStop, self).__init__(*args, **kwargs)
        self.glsl = time_stop
