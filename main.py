from __future__ import annotations
from kivy.config import Config
Config.set("kivy", "exit_on_escape", 0)

from kivy.app import App

from kivy.uix.widget import Widget
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import ScreenManager, Screen, CardTransition, NoTransition, SlideTransition
from kivy.uix.effectwidget import EffectWidget
from kivy.uix.label import Label

from kivy.graphics import Color, Rectangle, RoundedRectangle, Line

from kivy.clock import Clock
from kivy.animation import Animation
from kivy.utils import get_color_from_hex, platform
from tools.kivy_gradient import Gradient
from tools.shaders import TimeStop
from kivy.properties import StringProperty, NumericProperty, ListProperty, ColorProperty
from kivy.core.text import Label as CoreLabel
from kivy.metrics import dp

import plyer

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from kivy.uix.button import Button
    from kivy.uix.slider import Slider
    from kivy.uix.image import Image
    from kivy.uix.label import Label
    from kivy.uix.checkbox import CheckBox
    from kivy.uix.recycleview import RecycleView
    from kivy.uix.textinput import TextInput
    from kivy.uix.scrollview import ScrollView
    from kivy.input.motionevent import MotionEvent
    

import tools.common_vars as common_vars
from tools.music_playlist import MusicDatabase
import sys
import os
import numpy as np
import time
import struct
from glob import glob
from threading import Thread
from functools import partial



from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer, ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from kivy.graphics import (RenderContext, Fbo, Color, Rectangle,
                           Translate, PushMatrix, PopMatrix, ClearColor,
                           ClearBuffers)

class TrackLabelShader(Label):
    fs = StringProperty(None)
    vs = StringProperty(None)
    norm_text = StringProperty("") # This just holds the "normal" text that we wish to display
                                   # Otherwise if a string is being scrolled through, we set the text to
                                   # {some text} + " "*8 + {some text}
    label_type = StringProperty("")
    

    def __init__(self, **kwargs):
        """
        This label is assumed to be embedded in a ScrollView
        """
        super().__init__(**kwargs)
        self.canvas = RenderContext(use_parent_projection=True,
                                    use_parent_modelview=True)
        with open(f"{common_vars.app_folder}/tools/shaders/default_vertex_shader.glsl") as vs_fp:
            self.canvas.shader.vs = vs_fp.read()
        with open(f"{common_vars.app_folder}/tools/shaders/alpha_fade.glsl") as fs_fp:
            self.canvas.shader.fs = fs_fp.read()
        self.canvas["left_edge"] = 1.0
        self.canvas["right_edge"] = 2.0
        self.canvas.ask_update()
        self.max_width = 1
        self.is_animated = False
        self.animation_complete = False
    
    def on_parent(self, *args, **kwargs):
        self.main_display: MainDisplay = self.parent.parent
        self.scroll_view: ScrollView = self.parent
    
    def on_size(self, *args, **kwargs):
        self._update_alpha_fade_extents()
    
    def _update_alpha_fade_extents(self):
        if self.width > 0:
            max_width = self.main_display.width - 2 * (self.main_display.width * (1/2) - (self.main_display.width * (11/12) - dp(32)) / 2 + dp(2))
            if self.width > max_width:
                offset = self.scroll_view.scroll_x * (self.width - max_width)
                self.canvas["right_edge"] = (max_width + offset) / self.width
                self.canvas["left_edge"] = self.canvas["right_edge"] - (CoreLabel(font_size=self.main_display.height/35, bold=True).get_extents("...")[0] / self.width)
            else:
                self.canvas["left_edge"] = 1.0
                self.canvas["right_edge"] = 2.0
    
    def on_anim_start(self, *args):
        self.is_animated = True
        self.animation_complete = False
            
    def on_anim_progress(self, *args):
        self._update_alpha_fade_extents()
    
    def on_anim_complete(self, *args):
        self.scroll_view.scroll_x = 0
        self._update_alpha_fade_extents()
        self.animation_complete = True
        if self.label_type == "name":
            # Check if the artist name is animating and if so, make sure it is complete before restarting animations so that
            # both the name and artist animations are synced up
            if self.main_display.track_artist_label.is_animated and self.main_display.track_artist_label.animation_complete:
                self.main_display.track_name_anim_clock = Clock.schedule_once(partial(self.main_display.trigger_track_name_anim, self.norm_text), 5)
                self.main_display.track_artist_anim_clock = (
                    Clock.schedule_once(partial(self.main_display.trigger_track_artist_anim, self.main_display.track_artist_label.norm_text), 5)
                    )
            elif not self.main_display.track_artist_label.is_animated:
                self.main_display.track_name_anim_clock = Clock.schedule_once(partial(self.main_display.trigger_track_name_anim, self.norm_text), 5)
        elif self.label_type == "artist":
            # Check if the track name is animating and if so, make sure it is complete before restarting animations so that
            # both the name and artist animations are synced up
            if self.main_display.track_name_label.is_animated and self.main_display.track_name_label.animation_complete:
                self.main_display.track_name_anim_clock = (
                    Clock.schedule_once(partial(self.main_display.trigger_track_name_anim, self.main_display.track_name_label.norm_text), 5)
                    )
                self.main_display.track_artist_anim_clock = Clock.schedule_once(partial(self.main_display.trigger_track_artist_anim, self.norm_text), 5)
            elif not self.main_display.track_name_label.is_animated:
                self.main_display.track_artist_anim_clock = Clock.schedule_once(partial(self.main_display.trigger_track_artist_anim, self.norm_text), 5)

class MainScreen(Screen):
    
    def __init__(self, **kw):
        super().__init__(**kw)
        self.main_display = MainDisplay()
        self.add_widget(self.main_display)

        self.initialized = False
    
    def on_enter(self):
        if not self.initialized:
            self.main_display._frame_one_init()
            self.initialized = True

class MainDisplay(EffectWidget):
    next_track_event = None
    previous_track_event = None
    time_stop_event = False
    audio_clock = None
    track_name_anim_clock = None
    track_artist_anim_clock = None
    time_stop_effect = TimeStop()

    def __init__(self, **kwargs):

        # Custom EffectWidget stuff to give the fbo a stencil buffer
        self.canvas = RenderContext(use_parent_projection=True,
                                    use_parent_modelview=True)

        with self.canvas:
            self.fbo = Fbo(size=self.size, with_stencilbuffer=True)

        with self.fbo.before:
            PushMatrix()
        with self.fbo:
            ClearColor(0, 0, 0, 0)
            ClearBuffers()
            self._background_color = Color(*self.background_color)
            self.fbo_rectangle = Rectangle(size=self.size)
        with self.fbo.after:
            PopMatrix()

        super(EffectWidget, self).__init__(**kwargs)

        #Clock.schedule_interval(self._update_glsl, 0)

        fbind = self.fbind
        fbo_setup = self.refresh_fbo_setup
        fbind('size', fbo_setup)
        fbind('effects', fbo_setup)
        fbind('background_color', self._refresh_background_color)

        self.refresh_fbo_setup()
        self._refresh_background_color()  # In case this was changed in kwargs

        # Normal init stuff
        self.app: DndAudio = App.get_running_app()        

        # Setting up various widgets names
        self.time_slider: Slider = self.ids.audio_position_slider
        self.volume_slider: Slider = self.ids.volume_slider
        self.song_cover: Image = self.ids.song_cover
        self.background_img: Image = self.ids.background
        self.next_track_button: Button = self.ids.next_track
        self.previous_track_button: Button = self.ids.previous_track
        self.pause_button: Button = self.ids.pause
        self.shuffle_button: Button = self.ids.shuffle
        self.repeat_button: Button = self.ids.repeat
        self.track_pos_label: Label = self.ids.track_pos
        self.neg_track_pos_label: Label = self.ids.neg_track_pos
        self.track_name_label: TrackLabelShader = self.ids.track_name
        self.track_artist_label: TrackLabelShader = self.ids.track_artist
        self.track_name_scrollview: ScrollView = self.ids.track_name_scrollview
        self.track_artist_scrollview: ScrollView = self.ids.track_artist_scrollview

        self.stop_move = False # Stops gestures from working when grabbing the time slider
        self.update_time_pos = True # Stops time values from updating when grabbing the time slider
        self.song_cover_path = None

        self.c1 = get_color_from_hex("060606")
        self.c2 = get_color_from_hex("111111")

        self.background_img.texture = Gradient.vertical(self.c1, self.c2)
        self.time_slider_anim = Animation(pos=(0, 0), size=(0, 0))
        self.volume_slider_anim = Animation(pos=(0, 0), size=(0, 0))
        
        # only animate at 40fps since these animations are *expensive* operations on the poor cpu :(
        self.track_name_anim = Animation(scroll_x=0, duration=20, transition="linear", step=1/40)
        self.track_name_anim.on_start    = self.track_name_label.on_anim_start
        self.track_name_anim.on_progress = self.track_name_label.on_anim_progress
        self.track_name_anim.on_complete = self.track_name_label.on_anim_complete

        # only animate at 40fps since these animations are *expensive* operations on the poor cpu :(
        self.track_artist_anim = Animation(scroll_x=0, duration=20, transition="linear", step=1/40)
        self.track_artist_anim.on_start    = self.track_artist_label.on_anim_start
        self.track_artist_anim.on_progress = self.track_artist_label.on_anim_progress
        self.track_artist_anim.on_complete = self.track_artist_label.on_anim_complete

        # Clock.schedule_once(self._frame_zero_init, 0)

    
    def _frame_zero_init(self, dt):
        pass

    
    def _frame_one_init(self):
        self.orig_time_slider_pos = tuple(self.time_slider.pos) # These variables cannot be initialized before the first frame, init them here
        self.orig_time_slider_size = tuple(self.time_slider.size)

        self.orig_volume_slider_pos = tuple(self.volume_slider.pos)
        self.orig_volume_slider_size = tuple(self.volume_slider.size)

        try:
            pos, track_length, volume, speed = self.app.get_audioplayer_attr("init_pos", "track_length", "volume", "speed")
            
            self.time_slider.value = int(pos) / track_length * 1000
            self.volume_slider.value = round(volume * 100)

            self.update_track_info(0)

            # For some reason this doesn't get called properly when doing update_track_info
            # just call it here again
            self.update_time_text(pos, speed, track_length)

        except ValueError: # No config file case
            self.time_slider.value = 0
            self.volume_slider.value = 100
        


    def play_music(self):
        if self.audio_clock is None:
            self.app.start_audio_player()
            self.audio_clock = Clock.schedule_interval(self.update_track_info, 0.05)
            self.time_slider.disabled = False
            self.next_track_button.disabled = False
            self.previous_track_button.disabled = False
    
    def get_tap_type(self, touch: MotionEvent):
        self.button_touch = touch
        if touch.grab_current == self.next_track_button and self.next_track_event is None:
            self.next_track_event = Clock.schedule_once(lambda dt: self.play_next_track(self.button_touch), 0.2)
            return True
        elif touch.grab_current == self.previous_track_button and self.previous_track_event is None:
            self.previous_track_event = Clock.schedule_once(lambda dt: self.play_previous_track(self.button_touch), 0.2)
            return True


    def play_next_track(self, touch: MotionEvent):
        if touch.is_double_tap:
            self.app.change_track(transition="skip", direction="forward")
        else:
            self.app.change_track(transition="crossfade", direction="forward")

        self.next_track_event.cancel()
        self.next_track_event = None
    

    def play_previous_track(self, touch: MotionEvent):
        if touch.is_double_tap:
            self.app.change_track(transition="skip", direction="backward")
        else:
            self.app.change_track(transition="crossfade", direction="backward")

        self.previous_track_event.cancel()
        self.previous_track_event = None
    

    def pause_music(self):
        pause_flag, status = self.app.get_audioplayer_attr("pause_flag", "status")
        if status == "idle" and len(self.app.music_database) != 0:
            self.play_music()
        else:
            self.app.set_audioplayer_attr("pause_flag", not pause_flag)
            pause_flag = not pause_flag

        if len(self.app.music_database) != 0:
            if pause_flag:
                self.pause_button.background_normal = f"{common_vars.app_folder}/assets/buttons/play_normal.png"
                self.pause_button.background_down = f"{common_vars.app_folder}/assets/buttons/play_pressed.png"
                Animation.cancel_all(self.song_cover, "size")
                new_size = (self.height * (1/3), min(1/self.song_cover.image_ratio * self.height * (1/3), self.height * (1/3)))
                anim = Animation(size=new_size, duration=0.25, transition="out_back")
                anim.start(self.song_cover)

            else:
                self.pause_button.background_normal = f"{common_vars.app_folder}/assets/buttons/pause_normal.png"
                self.pause_button.background_down = f"{common_vars.app_folder}/assets/buttons/pause_pressed.png"
                Animation.cancel_all(self.song_cover, "size")
                new_size = (self.height * (5/12), min(1/self.song_cover.image_ratio * self.height * (5/12), self.height * (5/12)))
                anim = Animation(size=new_size, duration=0.25, transition="out_back")
                anim.start(self.song_cover)

    
    def begin_change_volume(self, touch: MotionEvent):

        if self.volume_slider.collide_point(*touch.pos) and not self.volume_slider.disabled:
            self.app.set_audioplayer_attr("volume", (self.volume_slider.value / 100))

            new_size = (self.orig_volume_slider_size[0] * 1.05, self.orig_volume_slider_size[1] * 2)
            new_pos = (self.orig_volume_slider_pos[0] - self.orig_volume_slider_size[0] * .025, self.orig_volume_slider_pos[1] - self.orig_volume_slider_size[1]/2)
            
            # Make the track pos bar expand when held
            self.volume_slider_anim.stop(self.volume_slider)
            self.volume_slider_anim = Animation(size=new_size, pos=new_pos, duration=0.3, transition="out_expo")
            self.volume_slider_anim.start(self.volume_slider)
    
    def update_volume(self, touch: MotionEvent):
        if touch.grab_current == self.volume_slider:
            self.app.set_audioplayer_attr("volume", (self.volume_slider.value / 100))
    
    def end_change_volume(self, touch: MotionEvent):
        if touch.grab_current == self.volume_slider:

            # Revert track pos to original position
            self.volume_slider_anim.stop(self.volume_slider)
            self.volume_slider_anim = Animation(size=self.orig_volume_slider_size, pos=self.orig_volume_slider_pos, duration=0.1, transition="linear")
            self.volume_slider_anim.start(self.volume_slider)
            
            self.app.set_audioplayer_attr("volume", (self.volume_slider.value / 100))


    def begin_seek(self, touch: MotionEvent):

        if self.time_slider.collide_point(*touch.pos) and not self.time_slider.disabled:
            self.stop_move = True
            self.update_time_pos = False
            new_size = (self.orig_time_slider_size[0] * 1.05, self.orig_time_slider_size[1] * 2)
            new_pos = (self.orig_time_slider_pos[0] - self.orig_time_slider_size[0] * .025, self.orig_time_slider_pos[1] - self.orig_time_slider_size[1]/2)

            track_length, speed = self.app.get_audioplayer_attr("track_length", "speed")
            # Force call set_value_pos since otherwise it's a frame behind and doesn't update when the slider is first grabbed
            self.time_slider.set_value_pos(touch.pos)
            self.update_time_text(self.time_slider.value * track_length / 1000, speed, track_length)

            # Make the track pos bar expand when held
            self.time_slider_anim.stop(self.time_slider)
            self.time_slider_anim = Animation(size=new_size, pos=new_pos, duration=0.3, transition="out_expo")
            self.time_slider_anim.start(self.time_slider)
    
    def update_seek(self, touch: MotionEvent):
        if touch.grab_current == self.time_slider:
            if self.time_slider.value == 1000:
                self.app.set_audioplayer_attr("seek_pos", 0)
            track_length, speed = self.app.get_audioplayer_attr("track_length", "speed")

            # Force call set_value_pos since otherwise it's a frame behind and results in unexpected
            # behavior when the slider is released
            self.time_slider.set_value_pos(touch.pos)
            self.update_time_text(self.time_slider.value * track_length / 1000, speed, track_length)

    def end_seek(self, touch: MotionEvent):
        if touch.grab_current == self.time_slider:
            self.stop_move = False

            # Revert track pos to original position
            self.time_slider_anim.stop(self.time_slider)
            self.time_slider_anim = Animation(size=self.orig_time_slider_size, pos=self.orig_time_slider_pos, duration=0.1, transition="linear")
            self.time_slider_anim.start(self.time_slider)
            
            track_length, speed, reverse_audio = self.app.get_audioplayer_attr("track_length", "speed", "reverse_audio")

            
            if self.time_slider.value == 1000 and not reverse_audio:
                pos = 0
            elif self.time_slider.value == 0 and reverse_audio:
                pos = track_length
            else:
                pos = self.time_slider.value * track_length / 1000
            
            self.app.set_audioplayer_attr("seek_pos", pos)
            self.app.set_audioplayer_attr("pos", pos)
            self.update_time_text(pos, speed, track_length)

            self.app.set_audioplayer_attr("status", "seek")
            self.update_time_pos = True
    
    def reverse(self):
        if len(self.app.music_database) != 0:
            reverse_audio, track_length = self.app.get_audioplayer_attr("reverse_audio", "track_length")
            self.app.set_audioplayer_attr("reverse_audio", not reverse_audio)
            seek_pos = int(self.time_slider.value * track_length / 1000)
            self.app.set_audioplayer_attr("seek_pos", seek_pos)
            self.app.set_audioplayer_attr("pos", seek_pos)
            self.app.set_audioplayer_attr("status", "seek")

    def toggle_repeat_mode(self):
        repeat_state = not self.app.music_database.repeat
        self.app.music_database.repeat = repeat_state
        if len(self.app.music_database) != 0:
            self.app.set_audioplayer_attr("next_track_id", self.app.music_database.peek_right(1))

        if repeat_state:
            self.repeat_button.background_normal = f"{common_vars.app_folder}/assets/buttons/repeat_active_normal.png"
            self.repeat_button.background_disabled_normal = f"{common_vars.app_folder}/assets/buttons/repeat_active_normal.png"
            self.repeat_button.background_down = f"{common_vars.app_folder}/assets/buttons/repeat_active_pressed.png"
            self.repeat_button.background_disabled_down = f"{common_vars.app_folder}/assets/buttons/repeat_active_pressed.png"

        else:
            self.repeat_button.background_normal = f"{common_vars.app_folder}/assets/buttons/repeat_inactive_normal.png"
            self.repeat_button.background_disabled_normal = f"{common_vars.app_folder}/assets/buttons/repeat_inactive_normal.png"
            self.repeat_button.background_down = f"{common_vars.app_folder}/assets/buttons/repeat_inactive_pressed.png"
            self.repeat_button.background_disabled_down = f"{common_vars.app_folder}/assets/buttons/repeat_inactive_pressed.png"
    
    def toggle_shuffle_mode(self):
        shuffle_state = not self.app.music_database._shuffle
        self.app.music_database.set_shuffle_state(shuffle_state)
        if len(self.app.music_database) != 0:
            self.app.set_audioplayer_attr("next_track_id", self.app.music_database.peek_right(1))

        if shuffle_state:
            self.shuffle_button.background_normal = f"{common_vars.app_folder}/assets/buttons/shuffle_active_normal.png"
            self.shuffle_button.background_disabled_normal = f"{common_vars.app_folder}/assets/buttons/shuffle_active_normal.png"
            self.shuffle_button.background_down = f"{common_vars.app_folder}/assets/buttons/shuffle_active_pressed.png"
            self.shuffle_button.background_disabled_down = f"{common_vars.app_folder}/assets/buttons/shuffle_active_pressed.png"

        else:
            self.shuffle_button.background_normal = f"{common_vars.app_folder}/assets/buttons/shuffle_inactive_normal.png"
            self.shuffle_button.background_disabled_normal = f"{common_vars.app_folder}/assets/buttons/shuffle_inactive_normal.png"
            self.shuffle_button.background_down = f"{common_vars.app_folder}/assets/buttons/shuffle_inactive_pressed.png"
            self.shuffle_button.background_disabled_down = f"{common_vars.app_folder}/assets/buttons/shuffle_inactive_pressed.png"
            
    def update_time_text(self, pos, speed, track_length):
        pos_time, neg_time = self._format_time(pos/1000/speed, track_length/1000/speed)
        self.track_pos_label.text = pos_time
        self.neg_track_pos_label.text = neg_time

    def update_track_info(self, dt): # Main track info update loop

        if len(self.app.music_database) == 0:
            self.track_name_label.text = "No songs found!"
            self.time_slider.disabled = True
            return

        # If we get a value error, these values don't exist in the audioplayer, thus skip this update
        try:
            status, pos, track_length, speed = self.app.get_audioplayer_attr("status", "pos", "track_length", "speed")
        except ValueError:
            return
        
        track = self.app.music_database.get_track() # Currently playing track

        # Update track position
        if self.update_time_pos:
            # If we aren't changing the size of our rectangle by more than ~1/3 a pixel, don't call an update (this is a surprisingly heavy graphics call)
            if abs((new_value := (int(pos) / track_length * 1000)) - self.time_slider.value) >= (1000 / self.time_slider.width / 3) and status != "idle":
                self.time_slider.value = new_value
            self.update_time_text(pos, speed, track_length)

        # Update track name / artist (and position if user is holding the time slider)
        if ((self.track_name_label.norm_text   != track["name"] 
        or   self.track_artist_label.norm_text != track["artist"]) 
        and  status in ("playing", "idle", "fade_in")):
            
            max_width = (self.width - 2 * (self.width * (1/2) - (self.width * (11/12) - dp(32)) / 2 + dp(2)))
            self.track_name_anim.stop(self.track_name_scrollview)
            self.track_artist_anim.stop(self.track_artist_scrollview)

            if self.track_name_anim_clock is not None:
                self.track_name_anim_clock.cancel()
            if self.track_artist_anim_clock is not None:
                self.track_artist_anim_clock.cancel()

            if CoreLabel(font_size=self.height/35, bold=True).get_extents(track["name"])[0] > max_width:
                self.track_name_label.is_animated = True
                self.track_name_label.animation_complete = False
                self.track_name_label.text = track["name"] + " "*8 + track["name"]
                self.track_name_anim_clock = Clock.schedule_once(partial(self.trigger_track_name_anim, track["name"]), 5)
            else:
                self.track_name_label.is_animated = False
                self.track_name_label.animation_complete = False
                self.track_name_label.text = track["name"]
            
            if CoreLabel(font_size=self.height/35, bold=True).get_extents(track["artist"])[0] > max_width:
                self.track_artist_label.is_animated = True
                self.track_artist_label.animation_complete = False
                self.track_artist_label.text = track["artist"] + " "*8 + track["artist"]
                self.track_artist_anim_clock = Clock.schedule_once(partial(self.trigger_track_artist_anim, track["artist"]), 5)
            else:
                self.track_artist_label.is_animated = False
                self.track_artist_label.animation_complete = False
                self.track_artist_label.text = track["artist"]

            self.track_name_label.norm_text = track["name"]
            self.track_artist_label.norm_text = track["artist"]
            
            if not self.update_time_pos:
                self.update_time_text(self.time_slider.value * track_length / 1000, speed, track_length)
        

        # Update song cover
        if self.song_cover_path != track["cover"] and status in ("playing", "idle", "fade_in"):
            if os.path.isfile(source := f"{common_vars.app_folder}/cache/covers/{track['persistent_id']}.jpg"):
                self.song_cover.source = source
            else:
                self.song_cover.source = f"{common_vars.app_folder}/assets/covers/default_cover.png"
            self.song_cover_path = track["cover"]

            # Don't do any cover size animations if we're not playing!!! This causes the size to small initially
            if status in ("playing", "fade_in"): 
                Animation.cancel_all(self.song_cover, "size")
                new_size = (self.height * (5/12), min(1/self.song_cover.image_ratio * self.height * (5/12), self.height * (5/12)))
                anim = Animation(size=new_size, duration=0, transition="linear")
                anim.start(self.song_cover)

            if self.song_cover.source != f"{common_vars.app_folder}/assets/covers/default_cover.png":
                new_colors = self.get_average_color()
            else:
                new_colors = (get_color_from_hex("060606"), 
                              get_color_from_hex("111111"))

            self._color_updater = self._generate_background_color((self.c1, self.c2), new_colors)
            self.background_updater = Clock.schedule_interval(self._update_background_color, 0.0333)
            self.c1, self.c2 = new_colors

    
    def trigger_track_name_anim(self, track_name, dt):
        scroll_px_distance = CoreLabel(font_size=self.height/35, bold=True).get_extents(track_name + " "*8)[0]

        # Choose A for the letter because why not
        approx_letter_size = CoreLabel(font_size=self.height/35, bold=True).get_extents("A")[0]
        new_scroll_x = self.track_name_scrollview.convert_distance_to_scroll(scroll_px_distance, 0)[0]
        self.track_name_anim._animated_properties = {"scroll_x" : new_scroll_x}

        # We want to scroll at about ~3 letters per second since that visually looks like a reasonable speed
        self.track_name_anim._duration = scroll_px_distance / (3*approx_letter_size)
        self.track_name_anim.start(self.track_name_scrollview)
    
    def trigger_track_artist_anim(self, track_artist, dt):
        scroll_px_distance = CoreLabel(font_size=self.height/35, bold=True).get_extents(track_artist + " "*8)[0]

        # Choose A for the letter because why not
        approx_letter_size = CoreLabel(font_size=self.height/35, bold=True).get_extents("A")[0]
        new_scroll_x = self.track_artist_scrollview.convert_distance_to_scroll(scroll_px_distance, 0)[0]
        self.track_artist_anim._animated_properties = {"scroll_x" : new_scroll_x}

        # We want to scroll at about ~3 letters per second since that visually looks like a reasonable speed
        self.track_artist_anim._duration = scroll_px_distance / (3*approx_letter_size)
        self.track_artist_anim.start(self.track_artist_scrollview)

    def get_average_color(self):

        color_array = np.ndarray(shape=(len(self.song_cover.texture.pixels)//4,4), dtype=np.uint8, buffer=self.song_cover.texture.pixels,  order="C").T

        avg_red = np.mean(color_array[0]) / 255
        avg_green = np.mean(color_array[1]) / 255
        avg_blue = np.mean(color_array[2]) / 255

        return ([avg_red*.2, avg_green*.2, avg_blue*.2, 1.0],
                [avg_red*.5, avg_green*.5, avg_blue*.5, 1.0])

    @staticmethod
    def _generate_background_color(old_color, new_color, *args):
        old_c1, old_c2 = old_color
        new_c1, new_c2 = new_color

        c1_transition = np.linspace(old_c1, new_c1, num=25, endpoint=True)
        c2_transition = np.linspace(old_c2, new_c2, num=25, endpoint=True)

        for c1, c2 in zip(c1_transition, c2_transition):
            yield c1, c2
        
    def _update_background_color(self, dt):
        try:
            # c1 is the bottom color, c2 is the top color
            c1, c2 = next(self._color_updater)
        except StopIteration:
            Clock.unschedule(self.background_updater)
        else:
            self.background_img.texture = Gradient.vertical(c1, c2)


    @staticmethod
    def _format_time(seconds, length):

        seconds = int(seconds)
        seconds_remaining = int(length) - seconds

        #print(seconds)

        if length < 3_600:
            pos_min = seconds // 60
            pos_sec = seconds % 60
            neg_min = seconds_remaining // 60
            neg_sec = seconds_remaining % 60
            pos = f"{pos_min}:{pos_sec:02d}"
            neg = f"-{neg_min}:{neg_sec:02d}"
        else:
            pos_hour = seconds // 3600
            pos_min = (seconds % 3600) // 60
            pos_sec = (seconds % 3600) % 60
            neg_hour = seconds_remaining // 3600
            neg_min = (seconds_remaining % 3600) // 60
            neg_sec = (seconds_remaining % 3600) % 60
            pos = f"{pos_hour}:{pos_min:02d}:{pos_sec:02d}"
            neg = f"-{neg_hour}:{neg_min:02d}:{neg_sec:02d}"

        return pos, neg
            
        
    def stop_time(self):
        
        if not self.time_stop_event and len(self.app.music_database) != 0: # Prevent this effect from playing twice in a row
            status, pos, track_length, speed, reverse_audio = self.app.get_audioplayer_attr("status", "pos", "track_length", "speed", "reverse_audio")
            if (status == "playing" and # Prevent this from playing during transitions and within 7 sec from end of song
            (not reverse_audio and (track_length - pos)/1000/speed > 7) or (reverse_audio and pos/1000/speed > 7)):
            
                self.update_uniforms(t0=-1)
                self.disabled = True
                self.effects = [self.time_stop_effect]
                self.glsl_clock = Clock.schedule_interval(self._update_glsl, 0)
                self.app.set_audioplayer_attr("status", "zawarudo")               
    
    def _start_time_effect(self):
        """
        Triggers when audioplayer is about to play the time stop sound
        """
        self.time_stop_event = True
        self.update_uniforms(t0=Clock.get_boottime())
        Clock.schedule_once(self.resume_time, 11.3)#9.3)
    
    def resume_time(self, dt):
        """
        Resets various settings when time resumes
        """
        self.effects = []
        self.glsl_clock.cancel()
        self.time_stop_event = False
        self.disabled = False
    
    def update_uniforms(self, **kwargs):
        for key, value in kwargs.items():
            self.canvas[key] = value
            for fbo in self.fbo_list:
                fbo[key] = value
      

class SongsScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.songs_display = SongsDisplay()
        self.add_widget(self.songs_display)
    
    # def on_enter(self, *args):
    #     print(self.children[0].song_list.data)
    #     if self.children[0].song_list.data is None:
    #         self.children[0].refresh_songs()
    #         print("adding data...")
    #     else:
    #         print("Not adding data...")

class SongsDisplayBase(Widget):

    # Same ListProperty as SortDropDown, see that for details
    dropdown_sort_settings = ListProperty()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.app: DndAudio = App.get_running_app()

        self.track_list: RecycleView = self.ids.track_list
        self.track_search: TextInput = self.ids.track_search
        self.sort_buttons: dict[str, SortButton] = {
            button.sort_by : button for button in self.ids.dropdown.container.children
            }

        self.sort_by = "date_added"
        self.reverse_sort = True
        try:
            self.sort_buttons[self.sort_by].sort_checkbox.active = True
        except KeyError:
            self.sort_buttons[self.sort_buttons.keys()[0]].sort_checkbox.active = True
        self.update_clock = None

        self.initalize_tracks()
    
    def back_button_on_release(self, *args):
        """
        Defined by subclass to choose what the back button does
        """

    def initalize_tracks(self):
        """
        Defined by subclass to load in items to recycleview
        """
        pass
        

    def on_text(self):
        """
        Only do our text searches after the user has stopped typing for 0.5 seconds (or if text field is empty, update immediately)
        """

        if self.track_search.text == "":
            dt = 0
        else:
            dt = 0.5

        if len(self.app.music_database) != 0:
            if self.update_clock is None:
                self.update_clock = Clock.schedule_once(self.update_track_list, dt)
            else:
                self.update_clock.cancel()
                self.update_clock = Clock.schedule_once(self.update_track_list, dt)
        
        width = self.track_search.width - self.track_search.padding[0] - self.track_search.padding[2]

        if self.track_search._lines_labels[0].size[0] < width:
            self.track_search.halign = "left"
            self.track_search.scroll_x = 0
        else:
            self.track_search.halign = "right"
    
    def change_sort(self, sort_by, reverse_sort):
        self.sort_by = sort_by
        self.reverse_sort = reverse_sort
        if len(self.app.music_database) != 0:
            self.refresh_tracks()
            self.app.set_audioplayer_attr("next_track_id", self.app.music_database.peek_right(1))

    def update_track_list(self, dt):
        
        self.refresh_tracks()
        self.track_list.scroll_y = 1
    
    def refresh_tracks(self):
        """
        Defined by subclass to load in items to recycleview
        """
        pass

    def sort_tracks(self, display=True):
        """
        Defined by subclass to sort displayed tracks (recycleview) and the valid_pointers list (music database)
        """
        pass


class SongsDisplay(SongsDisplayBase):

    def on_parent(self, *args):

        self.back_button: Button = self.ids.back_button
        self.back_button.text = "\u2039 Library"

        self.song_label: Label = self.ids.main_label
        self.song_label.text = "Songs"

        self.track_search.hint_text = "Search in Songs"
    
    def back_button_on_release(self, *args):
        self.parent.manager.transition.direction = "right"
        self.parent.manager.current = "library"


    def initalize_tracks(self):
        if len(self.app.music_database) != 0:
            self.track_list.data = [
                {   "track_id" : self.app.music_database.data["tracks"][track]["id"],
                 "playlist_id" : 0} 
                for track in self.app.music_database.data["tracks"].keys()
                ]
            
            self.sort_tracks()
    
    def refresh_tracks(self):
        if self.track_search.text == "":
            self.track_list.data = [
            {   "track_id" : self.app.music_database.data["tracks"][track]["id"],
             "playlist_id" : 0} 
            for track in self.app.music_database.data["tracks"].keys()
            ]
        
        else:
            self.track_list.data = [
            {   "track_id" : self.app.music_database.data["tracks"][track]["id"],
             "playlist_id" : 0}
            for track in self.app.music_database.data["tracks"].keys()
            if self.track_search.text.lower() in self.app.music_database.data["tracks"][track]["name"].lower()
            or self.track_search.text.lower() in self.app.music_database.data["tracks"][track]["artist"].lower()
            ]
            
        self.sort_tracks()
    
    def sort_tracks(self, display=True):
        
        if display:
            self.track_list.data.sort(
                key=lambda x : self.app.music_database.data["tracks"][x["track_id"]][self.sort_by], reverse=self.reverse_sort)
        if self.app.music_database.current_playlist_id == 0:
            self.app.music_database.valid_pointers.sort(
                key=lambda x : self.app.music_database.data["tracks"][x][self.sort_by], reverse=self.reverse_sort)


class SongButton(Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Get a reference to the app and main display
        self.app: DndAudio = App.get_running_app()
        self.main_display: MainDisplay = self.app.root.get_screen("main").main_display
        self.touch_down_called = False
        self.track_id: int # This gets set by the SongsDisplay/PlaylistSongsDisplay init
        self.playlist_id: int # This also gets set by the SongsDisplayBase/PlaylistSongsDisplay init

        self.name_label: Label = self.ids.name_label
        self.artist_label: Label = self.ids.artist_label
        self.song_cover: Image = self.ids.song_cover

    def on_parent(self, widget, parent):
        if parent is not None:
            self.name = self.app.music_database.data["tracks"][self.track_id]["name"]
            self.artist = self.app.music_database.data["tracks"][self.track_id]["artist"]
            self.persistent_id = self.app.music_database.data["tracks"][self.track_id]["persistent_id"]

            self.name_label.text = self.name
            self.artist_label.text = self.artist
            if os.path.isfile(f"{common_vars.app_folder}/cache/small_covers/{self.persistent_id}.jpg"):
                self.song_cover.source = f"{common_vars.app_folder}/cache/small_covers/{self.persistent_id}.jpg"
            else:
                self.song_cover.source = f"{common_vars.app_folder}/assets/covers/default_cover_small.png"

            self.songs_display: SongsDisplay | PlaylistSongsDisplay = self.parent.parent.parent
    
    def on_touch_down(self, touch: MotionEvent):
        if self.y < touch.y < self.y + self.height:
            self.touch_down_called = True
            self.background_color = 0.52941176, 0.52941176, 0.52941176, 0.71372549 # hex 878787B6
            return True

    def on_touch_up(self, touch: MotionEvent):
        self.background_color = 0, 0, 0, 1
        if self.y < touch.y < self.y + self.height and self.touch_down_called:
            status, pause_flag = self.app.get_audioplayer_attr("status", "pause_flag")

            self.app.music_database.set_playlist(self.playlist_id)
            self.songs_display.sort_tracks(display=False)

            if status == "idle":
                self.app.music_database.set_track(self.track_id)
                self.app.set_audioplayer_attr("track_id", self.track_id)
                self.app.set_audioplayer_attr("init_pos", 0)
                self.app.set_audioplayer_attr("pos", 0)
                # Need this to properly align the audioplayer since we not in the audio mainloop yet
                self.app.set_audioplayer_attr("next_track_id", self.app.music_database.peek_right(1))
                
                # Counterintuitively, pause_music() actually starts the music since audioplayer status is idle
                self.main_display.pause_music()

            elif pause_flag: # If music is paused and we select a new track, fade into the track, skipping the fade out of the current track.
                self.app.music_database.set_track(self.track_id)
                self.app.set_audioplayer_attr("track_id", self.track_id)
                self.main_display.pause_music()
                self.app.change_track(self.track_id, transition="fade_in")
            else:
                self.app.change_track(self.track_id, transition="crossfade")

            # This won't change the shuffle state, but it will re-shuffle the tracks when a new track is selected
            if self.app.music_database._shuffle:
                self.app.music_database.set_shuffle_state(self.app.music_database._shuffle)
            
            return True
        self.touch_down_called = False

class SortButton(BoxLayout):

    text = StringProperty()
    sort_by = StringProperty("date_added")
    background_color = ColorProperty()
    checkbox_group = StringProperty()

    def on_parent(self, widget, parent):
        # This function gets called (for some reason???) when removing widgets as well I guess
        # If this happens, ignore the rest of the function
        if parent is None: 
            return
        
        # self.songs_display = self.app.root.get_screen("songs").songs_display

        # fucked up way of getting the songs display since the above line doesn't work during init
        self.songs_display: SongsDisplay | PlaylistSongsDisplay = self.parent.parent.parent.parent

        # Reference to the dropdown in SongsDisplay
        self.songs_display_dropdown: DropDown = self.songs_display.ids.dropdown

        # This is handled in the dndaudio.kv file
        self.sort_checkbox: CheckBox
    
    def on_touch_down(self, touch: MotionEvent):
        if self.collide_point(*touch.pos):
            self.background_color = 0.52941176, 0.52941176, 0.52941176, 1 # hex 878787FF
            reverse_sort = self.sort_by in ("date_added", "play_date", "play_count")
            self.songs_display.change_sort(self.sort_by, reverse_sort)
            self.songs_display_dropdown.dismiss()
            self.sort_checkbox.active = True
            return True
    
    def on_touch_up(self, touch: MotionEvent):
        self.background_color = 0.2, 0.2, 0.2, 1

class SettingsScreen(Screen):

    def __init__(self, **kw):
        super().__init__(**kw)
        self.settings_display = SettingsDisplay()
        self.add_widget(self.settings_display)
    
class SettingsDisplay(Widget):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.fps_clock = Clock.schedule_interval(self.update_fps, 0.5)
        self.debug_clock = Clock.schedule_interval(self.get_debug_info, 0.05)
        self.speed_slider: Slider = self.ids.speed_slider
        self.fade_slider: Slider = self.ids.fade_slider
        self.fps_label: Label = self.ids.fps
        self.debug_info_label: Label = self.ids.debug_info

        self.app: DndAudio = App.get_running_app()

        # These will get overridden by config if the values exist
        self.speed_slider.value = 100
        self.fade_slider.value = 3000
    
    def change_speed(self, touch: MotionEvent):
        if touch.grab_current == self.speed_slider:
            #print(self.speed_slider.value, self.speed_slider.value / 100)
            self.app.set_audioplayer_attr("speed", np.float64(self.speed_slider.value / 100))
    

    def change_fade_duration(self, touch: MotionEvent):
        if touch.grab_current == self.fade_slider:
            self.app.set_audioplayer_attr("base_fade_duration", self.fade_slider.value)
            self.app.set_audioplayer_attr("fade_duration", int(self.fade_slider.value * (self.speed_slider.value / 100)))

    def update_fps(self, dt):
        fps = Clock.get_fps()
        self.fps_label.text = f"FPS: {fps:.3f}"
    
    def get_debug_info(self, dt):
        try:
            if self.app.music_database.current_playlist_id:
                self.debug_info_label.text = (
                "Debug Info:\n" +
                f"Current Playlist: {self.app.music_database.data["playlists"][self.app.music_database.current_playlist_id]["name"]}" +
                self.app.get_audioplayer_attr("debug_string")
                )
            else:
                self.debug_info_label.text = (
                "Debug Info:\n" +
                f"Current Playlist: None" +
                self.app.get_audioplayer_attr("debug_string")
                )
        except:
            pass

class LibraryScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.library_display = LibraryDisplay()
        self.add_widget(self.library_display)

class LibraryDisplay(Widget):
    pass


class PlaylistsScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.playlist_display = PlaylistsDisplay()
        self.add_widget(self.playlist_display)


class PlaylistsDisplay(Widget):
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.app: DndAudio = App.get_running_app()
        self.playlists: RecycleView = self.ids.playlists
        
        if len(self.app.music_database) != 0:
            self.playlists.data = [
                {"playlist_id" : playlist_id, "sort_by" : "order", "reverse_sort" : False}
                for playlist_id in self.app.music_database.data["playlists"].keys()
                ]
            

class PlaylistSongsScreen(Screen):
    def __init__(self, playlist_id, sort_by="order", reverse_sort=False, **kwargs):
        super().__init__(**kwargs)
        self.playlist_songs_display = PlaylistSongsDisplay(playlist_id, sort_by=sort_by, reverse_sort=reverse_sort)
        self.add_widget(self.playlist_songs_display)
    
    def on_leave(self, *args):
        self.manager.remove_widget(self)

class PlaylistSongsDisplay(SongsDisplayBase):

    def __init__(self, playlist_id, sort_by="order", reverse_sort=False, **kwargs):
        self.playlist_id = playlist_id
        super(SongsDisplayBase, self).__init__(**kwargs)

        self.app: DndAudio = App.get_running_app()

        self.track_list: RecycleView = self.ids.track_list
        self.track_search: TextInput = self.ids.track_search
        self.sort_buttons: dict[str, SortButton] = {
            button.sort_by : button for button in self.ids.dropdown.container.children
            }
        

        self.sort_by = sort_by
        self.reverse_sort = reverse_sort
        try:
            self.sort_buttons[self.sort_by].sort_checkbox.active = True
        except KeyError:
            self.sort_buttons[self.sort_buttons.keys()[0]].sort_checkbox.active = True
        self.update_clock = None

        self.initalize_tracks()
    
    def on_parent(self, *args):

        self.back_button: Button = self.ids.back_button
        self.back_button.text = "\u2039 Playlists"

        self.playlist_label: Label = self.ids.main_label
        self.playlist_label.text = self.app.music_database.data["playlists"][self.playlist_id]["name"]

        self.track_search.hint_text = "Search in Playlist"
    
    def back_button_on_release(self, *args):
        self.parent.manager.transition.direction = "right"
        self.parent.manager.current = "playlists"
        

    def initalize_tracks(self):
        if len(self.app.music_database) != 0:
            self.track_list.data = [
                {   "track_id" : track_id,
                 "playlist_id" : self.playlist_id}
                for track_id in self.app.music_database.data["playlists"][self.playlist_id]["track_list"]
                ]
            
            self.sort_tracks()
    
    def refresh_tracks(self):
        if self.track_search.text == "":
            self.track_list.data = [
            {   "track_id" : track_id,
             "playlist_id" : self.playlist_id} 
            for track_id in self.app.music_database.data["playlists"][self.playlist_id]["track_list"]
            ]
        
        else:
            self.track_list.data = [
            {   "track_id" : track_id,
             "playlist_id" : self.playlist_id} 
            for track_id in self.app.music_database.data["playlists"][self.playlist_id]["track_list"]
            if self.track_search.text.lower() in self.app.music_database.data["tracks"][track_id]["name"].lower()
            or self.track_search.text.lower() in self.app.music_database.data["tracks"][track_id]["artist"].lower()
            ]
            
        self.sort_tracks()
    
    def sort_tracks(self, display=True):
        if self.sort_by == "order":
            if display:
                self.track_list.data.sort(
                    key=lambda x : self.app.music_database.data["playlists"][self.playlist_id]["track_list"].index(x["track_id"]), reverse=self.reverse_sort)
            if self.app.music_database.current_playlist_id == self.playlist_id:
                self.app.music_database.valid_pointers.sort(
                    key=lambda x : self.app.music_database.data["playlists"][self.playlist_id]["track_list"].index(x), reverse=self.reverse_sort)
        else:
            if display:
                self.track_list.data.sort(
                    key=lambda x : self.app.music_database.data["tracks"][x["track_id"]][self.sort_by], reverse=self.reverse_sort)
            if self.app.music_database.current_playlist_id == self.playlist_id:
                self.app.music_database.valid_pointers.sort(
                    key=lambda x : self.app.music_database.data["tracks"][x][self.sort_by], reverse=self.reverse_sort)
    
    def change_sort(self, sort_by, reverse_sort):
        super().change_sort(sort_by, reverse_sort)
        recycleview_index = next((i for i, data in enumerate(self.app.root.get_screen("playlists").playlist_display.playlists.data) if data["playlist_id"] == self.playlist_id), None)
        if recycleview_index is not None:
            self.app.root.get_screen("playlists").playlist_display.playlists.data[recycleview_index] = (
            {"playlist_id" : self.playlist_id, "sort_by" : self.sort_by, "reverse_sort" : self.reverse_sort}
            )


class ScreenSelectionButton(Widget):

    # This is just the name of the screen that we link to
    screen_link_name = StringProperty("")
    transition_direction = StringProperty("left")

    # The text displayed for the button
    display_name = StringProperty("")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.touch_down_called = False
    
    # Wait until labels are initialized to set values
    def on_kv_post(self, *args):
        
        self.app: DndAudio = App.get_running_app()
        self.display_label: Label = self.ids.screen_label
        self.display_label.text = self.display_name
        
    def on_touch_down(self, touch: MotionEvent):
        if self.y < touch.y < self.y + self.height:
            self.touch_down_called = True
            self.background_color = 0.52941176, 0.52941176, 0.52941176, 0.71372549 # hex 878787B6
            return True

    def on_touch_up(self, touch: MotionEvent):
        self.background_color = 0, 0, 0, 1
        if self.y < touch.y < self.y + self.height and self.touch_down_called:
            self.app.root.transition.direction = self.transition_direction
            self.app.root.current = self.screen_link_name
            return True
        self.touch_down_called = False

class PlaylistButton(ScreenSelectionButton):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.playlist_id: int # these get set by PlaylistsDisplay init
        self.sort_by: str
        self.reverse_sort: bool

    def on_parent(self, *args):
        self.app: DndAudio = App.get_running_app()

        self.display_label: Label = self.ids.screen_label
        self.display_label.text = self.app.music_database.data["playlists"][self.playlist_id]["name"]
        self.screen_link_name = self.app.music_database.data["playlists"][self.playlist_id]["persistent_id"]

    def on_touch_up(self, touch: MotionEvent):
        self.background_color = 0, 0, 0, 1
        if self.y < touch.y < self.y + self.height and self.touch_down_called:
            self.app.root.add_widget(PlaylistSongsScreen(self.playlist_id, sort_by=self.sort_by, 
                                                         reverse_sort=self.reverse_sort, name=self.screen_link_name))
            self.app.root.transition.direction = self.transition_direction
            self.app.root.current = self.screen_link_name
            return True
        self.touch_down_called = False

class DndAudio(App):

    height = NumericProperty(0)
    width = NumericProperty(0)

    def build(self):

        self.osc_returns = None

        self.dispatcher = Dispatcher()
        self.dispatcher.map("/return", self._return_message)
        self.dispatcher.map("/call/*", self.call_function)
        self.dispatcher.map("/kill", self.stop)

        self.osc_server = BlockingOSCUDPServer(("127.0.0.1", 8000), self.dispatcher)
        self.osc_client = SimpleUDPClient("127.0.0.1", 8001)

        self.filechooser: plyer.facades.FileChooser = plyer.filechooser

        #self.reload_songs()

        # We haven't been using reload_songs for quite some time, leave it for later reworks
        self.music_database = MusicDatabase(common_vars.music_database_path)
        # This won't work on android
        if platform in ('linux', 'linux2', 'macos', 'win') and len(self.music_database) != 0:
            self.music_database.cache_covers()
        elif platform == "android":
            from android.permissions import request_permissions, Permission
            request_permissions([Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE])
        
        self.start_service()     

        def nothing(*args, **kwargs):
            pass

        # In kivy versions 2.1.0 - 2.3.1 (current), on_dropfile is fucked up 
        # and gets called instead, despite being depreceated.
        # Set this to be a function that does absolutely nothing 
        # so the on_drop_file event works correctly.
        Window.on_dropfile = nothing
        Window.bind(on_drop_file=self._file_drop_handler)
        Window.bind(on_resize=self.on_resize)
        if platform != "android":
            Window.size = (375, 700)
        
        self.root: ScreenManager
        sm = ScreenManager(transition=NoTransition())
        sm.add_widget(SongsScreen(name="songs"))
        sm.add_widget(MainScreen(name="main"))
        sm.add_widget(SettingsScreen(name="settings"))
        sm.add_widget(LibraryScreen(name="library"))
        sm.add_widget(PlaylistsScreen(name="playlists"))
        sm.transition.direction = "right"
        sm.current = "main"
        sm.transition = SlideTransition()
        return sm

    def on_resize(self, window, width, height):
        self.width = width
        self.height = height

    def start_audio_player(self):
        self.set_audioplayer_attr("status", "playing")


    def change_track(self, track_id=None, transition="crossfade", direction="forward"):

        if track_id is None:
            if direction == "forward":
                self.music_database >> 1
                self.set_audioplayer_attr("next_track_id", self.music_database.get_track()["id"])
            elif direction == "backward":
                self.music_database << 1
                self.set_audioplayer_attr("next_track_id", self.music_database.get_track()["id"])
        else:
            self.music_database.set_track(track_id)
            self.set_audioplayer_attr("next_track_id", self.music_database.get_track()["id"])

        if transition == "crossfade":
            self.set_audioplayer_attr("status", "change_track")
        
        elif transition == "fade_in":
            self.set_audioplayer_attr("status", "fade_in")

        elif transition == "skip":
            self.set_audioplayer_attr("status", "skip")

    
    def reload_songs(self):
        """
        Updates the currently available audio files in the audio folder on PC / Music folder on android. Only looks for .wav + .ogg files for now
        """
        
        loaded_songs = []

        if platform in ('linux', 'linux2', 'macos', 'win'):
            loaded_songs = glob(f"{common_vars.app_folder}/audio/*.wav") + glob(f"{common_vars.app_folder}/audio/*.ogg") + glob(f"{common_vars.app_folder}/audio/*.mp3")

        elif platform == "android":
            from jnius import autoclass, cast
            from jnius.jnius import JavaException
            from android.permissions import request_permissions, Permission

            request_permissions([Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE])
            
            # Initialize and get context
            mActivity = autoclass('org.kivy.android.PythonActivity').mActivity
            currentActivity = cast('android.app.Activity', mActivity)
            Context = cast('android.content.ContextWrapper', currentActivity.getApplicationContext())

            # Get MediaStore classes
            MediaStore = autoclass("android.provider.MediaStore")
            MediaStoreAudioMedia = autoclass("android.provider.MediaStore$Audio$Media")

            # Get Android version
            version = autoclass("android.os.Build$VERSION")
            sdk_int = version.SDK_INT
            version_codes = autoclass("android.os.Build$VERSION_CODES")
            Q = version_codes.Q

            loaded_songs = []  # List to store loaded songs

            # Function to query and load songs from a specific folder
            def query_and_load_songs(content_uri):
                try:
                    # Set the selection to filter by the directory path
                    selection = "_data LIKE ? OR _data LIKE ?"
                    # Assuming the "Music" folder is located somewhere in the path
                    selectionArgs = ["%/Music/%%.wav", "%/Music/%%.ogg"]

                    cursor = Context.getContentResolver().query(
                        content_uri, None, selection, selectionArgs, None
                    )
                    if cursor is not None:
                        #column_names = cursor.getColumnNames()
                        #print(f"Available columns: {column_names}")

                        while cursor.moveToNext():
                            try:
                                # Use '_display_name' to get the name of the file
                                audioIndex = cursor.getColumnIndex("_data")
                                if audioIndex != -1:
                                    song_name = cursor.getString(audioIndex)
                                    if song_name is not None:
                                        loaded_songs.append(song_name)
                                        print(f"Loaded song: {song_name}")
                                    else:
                                        print("Song name is null.")
                                else:
                                    print("Audio index not found.")
                            except JavaException as e:
                                print(f"Error accessing cursor: {e}")
                        cursor.close()
                    else:
                        print("Cursor is null. Content URI might be incorrect or no data available.")
                except JavaException as e:
                    print(f"JavaException during query: {e}")

            # Query external storage, assuming music files are generally stored there
            if sdk_int >= Q:
                query_and_load_songs(MediaStoreAudioMedia.getContentUri(MediaStore.VOLUME_EXTERNAL))
            else:
                query_and_load_songs(MediaStoreAudioMedia.EXTERNAL_CONTENT_URI)

            print(f"Songs loaded from MediaStore: {loaded_songs}")

        self.music_database = MusicDatabase(common_vars.music_database_path)
        # for file in loaded_songs:
        #     database_files = [self.music_database.data["tracks"][track]["file"] for track in self.music_database.data["tracks"].keys()]
        #     if not any([os.path.samefile(file, database_file) for database_file in database_files]):
        #         self.music_database.add_track(file)
        # This won't work on android
        if platform in ('linux', 'linux2', 'macos', 'win'):
            self.music_database.cache_covers()


    def start_service(self):
        if platform == "android":
            from jnius import autoclass
            self.audio_service = autoclass('org.mrrp.dndaudioplayer.ServiceAudioplayer')
            mActivity = autoclass('org.kivy.android.PythonActivity').mActivity
            argument = ''
            self.audio_service.start(mActivity, argument)

        elif platform in ('linux', 'linux2', 'macos', 'win'):
            from runpy import run_path
            from multiprocessing import Process, set_start_method
            set_start_method("spawn")
            self.audio_service = Process(target=run_path, args=(f"{common_vars.app_folder}/tools/audioplayer.py",), daemon=True)
            self.audio_service.start()

        else:
            raise NotImplementedError("Platform not supported")
    
    def stop_service(self):
        if platform == "android":
            from jnius import autoclass
            mActivity = autoclass('org.kivy.android.PythonActivity').mActivity
            self.audio_service.stop(mActivity)
        
        else:
            self.audio_service.kill()
    
    
    def _file_drop_handler(self, window, file_path: bytes, x, y, *args):
        self.music_database.add_track(file_path.decode("utf-8"))
        self.call_audioplayer_func("reload_track_data")
        self.root.get_screen("songs").songs_display.refresh_tracks()
    
    def _add_file_handler(self, file_paths: list[str] | None):
        if file_paths is not None:
            for file_path in file_paths:
                self.music_database.add_track(file_path)
            self.call_audioplayer_func("reload_track_data")
            self.root.get_screen("songs").songs_display.refresh_tracks()


    def _return_message(self, address: str, *values):
        if len(values) == 1:
            self.osc_returns = values[0]
        else:
            self.osc_returns = values

    def call_function(self, address: str, func_name: str, *args):
        if address == "/call/music_database":
            func = getattr(self.music_database, func_name)
        else:
            screen, widget_name = address.split("/")[2:]
            widget = getattr(self.root.get_screen(screen), widget_name)
            func = getattr(widget, func_name)
        if args and isinstance(args[-1], str) and args[-1][0] == "&":
            self.set_audioplayer_attr(args[-1][1:], func(*args[:-1]))
        else:
            func(*args)
        self.osc_server.handle_request()
    
    def get_audioplayer_attr(self, *args):
        self.osc_client.send_message("/read", args)
        self.osc_server.handle_request()
        return self.osc_returns
    
    def set_audioplayer_attr(self, attr, value):
        self.osc_client.send_message("/write", (attr, value))
    
    def call_audioplayer_func(self, func_name, *args):
        self.osc_client.send_message("/call", (func_name, *args))
        self.osc_server.handle_request()
        return self.osc_returns

    def save_config(self):
        if len(self.music_database) == 0:
            return
        try:
            with open(f"{common_vars.app_folder}/config", "wb") as fp:
                (track_id, track_pos, track_length, total_frames, 
                 speed, fade_duration, volume, reverse_audio) = self.config_vars
                main_display: MainDisplay = self.root.get_screen("main").main_display
                # Add special code so we know the file format is ok
                fp.write(bytes([0x6D, 0x72, 0x72, 0x70])) # mrrp
                fp.write(int(track_id).to_bytes(4, "little"))
                fp.write(int(track_pos).to_bytes(4, "little"))
                fp.write(int(total_frames).to_bytes(8, "little"))
                fp.write(int(reverse_audio).to_bytes(1, "little"))
                fp.write(int(self.music_database._shuffle).to_bytes(1, "little"))
                fp.write(int(self.music_database.repeat).to_bytes(1, "little"))
                fp.write(self.sort_by_bytemap[self.root.get_screen("songs").songs_display.sort_by])
                for channel_val in main_display.c1 + main_display.c2: # c1 and c2 should be lists of floats
                    fp.write(round(channel_val * 255).to_bytes(1, "little"))
                fp.write(int(fade_duration).to_bytes(2, "little"))
                fp.write(struct.pack("3d", track_length, speed, volume))
        except Exception as err:
            if os.path.exists(f"{common_vars.app_folder}/config"):
                os.remove(f"{common_vars.app_folder}/config")
    
    def load_config(self):
        try:
            with open(f"{common_vars.app_folder}/config", "rb") as fp:
                file_tag = fp.read(4)
                track_id = int.from_bytes(fp.read(4), "little")
                track_pos = int.from_bytes(fp.read(4), "little")
                total_frames = int.from_bytes(fp.read(8), "little")
                reverse_audio = int.from_bytes(fp.read(1), "little")
                shuffle = int.from_bytes(fp.read(1), "little")
                repeat = int.from_bytes(fp.read(1), "little")
                sort_by = self.reverse_sort_by_bytemap[fp.read(1)]
                c1 = np.ndarray(shape=(4,), dtype=np.uint8, buffer=fp.read(4)) / 255
                c2 = np.ndarray(shape=(4,), dtype=np.uint8, buffer=fp.read(4)) / 255
                fade_duration = int.from_bytes(fp.read(2), "little")
                track_length, speed, volume = struct.unpack("3d", fp.read(24))
                
        except Exception as err:
            return
        if track_id in self.music_database.valid_pointers and file_tag == bytes([0x6D, 0x72, 0x72, 0x70]): # make sure track id still exists
            #self.set_audioplayer_attr("pause_flag", True)
            self.music_database.set_track(track_id)
            found_track = True
        else:
            found_track = False
        if file_tag == bytes([0x6D, 0x72, 0x72, 0x70]):

            main_display: MainDisplay = self.root.get_screen("main").main_display

            if found_track:
                # If we can't find the track, don't set track position values since these aren't valid
                self.set_audioplayer_attr("init_pos", track_pos)
                self.set_audioplayer_attr("pos", track_pos) # This (technically) shouldn't do anything, but it prevents time slider visual glitches on startup
                self.set_audioplayer_attr("total_frames", total_frames)
                self.set_audioplayer_attr("track_length", track_length)
                main_display.c1 = list(c1) # main_display has it's own default values on init, but these will override that
                main_display.c2 = list(c2)
            self.set_audioplayer_attr("speed", speed)
            self.set_audioplayer_attr("base_fade_duration", fade_duration)
            self.set_audioplayer_attr("fade_duration", int(fade_duration * speed))
            self.set_audioplayer_attr("volume", volume)
            self.set_audioplayer_attr("reverse_audio", reverse_audio)

            if shuffle: # MusicDatabase._shuffle is set to False by default, so if shuffle is true, flip value
                main_display.toggle_shuffle_mode()
            if repeat: # MusicDatabase.repeat is set to False by default, so if repeat is true, flip value
                main_display.toggle_repeat_mode()

            songs_display: SongsDisplay = self.root.get_screen("songs").songs_display
            songs_display.sort_by = sort_by
            songs_display.reverse_sort = sort_by in ("date_added", "play_date", "play_count")
            songs_display.sort_buttons[sort_by].sort_checkbox.active = True
            songs_display.refresh_tracks()

            settings_display: SettingsDisplay = self.root.get_screen("settings").settings_display
            settings_display.speed_slider.value = speed * 100
            settings_display.fade_slider.value = fade_duration

            # This waits until the audioplayer has fully received all of the config messages
            while self.get_audioplayer_attr("reverse_audio") == ():
                time.sleep(0.001)

    def on_pause(self, *args):
        return True
    
    def on_start(self, *args):

        while self.osc_returns != "ready": # Wait for audioplayer to finish its init
            self.osc_server.timeout = 0.5
            self.osc_server.handle_request()
        
        self.osc_server.timeout = None

        self.sort_by_bytemap = {
                "date_added" : b'\x00',
                      "name" : b'\x01',
                    "artist" : b'\x02',
                 "play_date" : b'\x03',
                "play_count" : b'\x04'
            }
        self.reverse_sort_by_bytemap = {v : k for k, v in self.sort_by_bytemap.items()}

        if os.path.exists(f"{common_vars.app_folder}/config") and len(self.music_database) != 0:
            self.load_config()
        if len(self.music_database) != 0:
            self.set_audioplayer_attr("track_id", self.music_database.track_pointer)
            self.set_audioplayer_attr("next_track_id", self.music_database.peek_right(1))

        self.config_vars = self._get_config_vars(0)
        self.config_clock = Clock.schedule_interval(self._get_config_vars, 1)
    
    def _get_config_vars(self, dt):
        self.config_vars = self.get_audioplayer_attr("track_id", "pos", "track_length", "total_frames", 
                                                     "speed", "base_fade_duration", "volume", "reverse_audio")

    def on_stop(self, *args):
        self.save_config()

if __name__ == '__main__':
    from kivy.core.window import Window
    from kivy.uix.dropdown import DropDown


    # Because DropDown secretly calls `from kivy.core.window import Window`
    # We need to place the DropDown stuff in the if __name__ == "__main__" block so when
    # we start the audioplayer (since it will rerun this script), it won't create a second blank window
    class SortDropDown(DropDown):

        sort_settings = ListProperty()
        """
        sort_settings expects a list of dictionaries of the following format:

        {
               "text" : text to display for the SortButton,
            "sort_by" : sort_by parameter for the SortButton
        }

        Example for 3 buttons:
        sort_settings = [
            {"text" : "Name", "sort_by" : "name"},
            {"text" : "Artist", "sort_by" : "artist"},
            {"text" : "Date Added", "sort_by" : "date_added"}
        ]

        Buttons are added in order top to bottom for the sort_settings list
        """

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.app: DndAudio = App.get_running_app()
            self.canvas_initialized = False

            # Basically scheduling the canvas update for frame 1
            Clock.schedule_once(self._frame_zero_init, 0)

        def _frame_zero_init(self, dt):
            
            # Basically scheduling the canvas update for frame 1
            Clock.schedule_once(lambda dt: self._update_canvas(), 0)
        
        def on_sort_settings(self, widget, new_sort_settings):
            self.dismiss()

            for child in self.container.children.copy():
                self.remove_widget(child)

            for settings in new_sort_settings:
                button = SortButton(
                    size_hint_y=None,
                    height=(CoreLabel(font_size=self.app.height/40).get_extents("_")[1]+12),
                )

                button.text = settings["text"]
                button.sort_by = settings["sort_by"]
                button.background_color = (0.2, 0.2, 0.2, 1)
                button.checkbox_group = hex(id(widget))

                self.add_widget(button)
            
            if self.canvas_initialized:
                self._update_canvas()
        
        def _update_canvas(self):
            self.canvas_initialized = True
            for i, button in enumerate(self.container.children):
                button: SortButton

                # Child widgets are listed backwards relative to how they were added, so 
                # i == 0 is the bottom widget and i == num_children is the top widget
                if len(self.container.children) > 1: 
                    if i == 0:
                        with button.canvas.before:
                            Color(*button.background_color)
                            RoundedRectangle(size=button.size, pos=button.pos, radius=(0, 0, button.height/3, button.height/3))

                    elif i < len(self.container.children) - 1:
                        with button.canvas.before:
                            Color(*button.background_color)
                            Rectangle(size=button.size, pos=button.pos)
                            Color(1, 1, 1, 1)
                            Line(width=1, points=(button.x, button.y, button.x + button.width, button.y))
                    else:
                        with button.canvas.before:
                            Color(*button.background_color)
                            RoundedRectangle(size=button.size, pos=button.pos, radius=(button.height/3, button.height/3, 0, 0))
                            Color(1, 1, 1, 1)
                            Line(width=1, points=(button.x, button.y, button.x + button.width, button.y))
                else:
                    with button.canvas.before:
                        Color(*button.background_color)
                        RoundedRectangle(size=button.size, pos=button.pos, radius=(button.height/3, button.height/3, button.height/3, button.height/3))

    DndAudio().run()
        
