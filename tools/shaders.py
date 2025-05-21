from kivy.event import EventDispatcher
from kivy.properties import (StringProperty, ObjectProperty)
from kivy.uix.effectwidget import EffectBase
import tools.common_vars as common_vars

class ShaderEffectBase(EventDispatcher):
    """
    I'm basically copying a lot from kivy EffectBase, but I want to change a bunch of it.
    Otherwise this is a base class for GLSL shader stuff
    """
    glsl = StringProperty("")
    source = StringProperty("")
    fbo = ObjectProperty(None, allownone=True)

    def __init__(self, *args, **kwargs):
        super(ShaderEffectBase, self).__init__(*args, **kwargs)
        fbind = self.fbind
        fbo_shader = self.set_fbo_shader
        fbind("fbo", fbo_shader)
        fbind("glsl", fbo_shader)
        fbind("source", self._load_from_source)

    def set_fbo_shader(self, *args):
        '''Sets the :class:`~kivy.graphics.Fbo`'s shader by splicing
        the :attr:`glsl` string into a full fragment shader.

        The full shader is made up of :code:`shader_header +
        shader_uniforms + self.glsl + shader_footer_effect`.
        '''
        if self.fbo is None:
            return
        self.fbo.set_fs(self.glsl)

    def _load_from_source(self, *args):
        '''(internal) Loads the glsl string from a source file.'''
        source = self.source
        if not source:
            return
        with open(source) as fp:
            self.glsl = fp.read()

class TimeStop(ShaderEffectBase):
    def __init__(self, *args, **kwargs):
        super(TimeStop, self).__init__(*args, **kwargs)
        self.source = f"{common_vars.app_folder}/tools/shaders/time_stop.glsl"

        
