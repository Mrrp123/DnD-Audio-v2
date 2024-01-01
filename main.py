from kivy.app import App
from kivy.core.window import Window
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.screenmanager import ScreenManager, Screen, CardTransition, NoTransition, SlideTransition
from kivy.properties import StringProperty, BooleanProperty, ObjectProperty, ListProperty
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.animation import Animation
from kivy.utils import get_color_from_hex, platform
from tools.kivy_gradient import Gradient

import tools.common_vars as common_vars
from tools.audioplayer import AudioPlayer
import sys
import os
import numpy as np
import time
import struct

def start_audio_player():
    common_vars.audioplayer_thread.start()

def change_song(file=None, transition="fade", direction="forward"):

    if file is None:
        if direction == "forward":
            common_vars.audioplayer.next_song_data = common_vars.audioplayer.playlist >> 1
        elif direction == "backward":
            common_vars.audioplayer.next_song_data = common_vars.audioplayer.playlist << 1
    else:
        common_vars.audioplayer.next_song_data = common_vars.audioplayer.playlist.set_song(file)

    if transition == "fade":
        common_vars.audioplayer.status = "change_song"

    elif transition == "skip":
        common_vars.audioplayer.status = "skip"



class MainScreen(Screen):
    
    def __init__(self, **kw):
        super().__init__(**kw)
        self.add_widget(MainDisplay())

class MainDisplay(Widget):
    next_song_event = None
    previous_song_event = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.audioplayer: AudioPlayer = common_vars.audioplayer

        self.time_slider = self.ids.audio_position_slider
        self.stop_move = False # Stops gestures from working when grabbing the time slider
        self.update_time_pos = True # Stops time values from updating when grabbing the time slider
        self.song_cover = self.ids.song_cover

        self.c1 = "060606"
        self.c2 = "111111"

        self.ids.background.texture = Gradient.vertical(get_color_from_hex(self.c1), get_color_from_hex(self.c2))

    
    def on_parent(self, *args, **kwargs):
        if os.path.exists("./config"):
            self.load_config()
    

    def load_config(self):
        try:
            with open("./config", "rb") as fp:
                file_len = int.from_bytes(fp.read(2), "little")
                song_file = fp.read(file_len).decode("utf-8")
                song_pos = int.from_bytes(fp.read(4), "little")
                total_frames = int.from_bytes(fp.read(8), "little")
                song_length, speed = struct.unpack("2d", fp.read(16))
                fade_duration = int.from_bytes(fp.read(2), "little")
        except:
            return
        if os.path.exists(song_file): # make sure song file still exists
            self.audioplayer.pause_flag = True
            self.audioplayer.song_data = self.audioplayer.playlist.set_song(song_file)
            self.audioplayer.init_pos = song_pos
            self.audioplayer.song_length = song_length
            self.audioplayer.total_frames = total_frames
            self.audioplayer.speed = speed
            self.audioplayer.base_fade_duration = fade_duration
            self.audioplayer.fade_duration = int(fade_duration * speed)
            self.update_song_info(0)
            self.play_music()
    
            

    def play_music(self):
        start_audio_player()
        self.audio_clock = Clock.schedule_interval(self.update_song_info, 0.05)
        self.time_slider.disabled = False
        self.ids.next_song.disabled = False
        self.ids.previous_song.disabled = False
    

    def get_tap_type(self, button, touch):
        if touch.grab_current == button:
            self.button_touch = touch
            for id, widget in button.parent.ids.items():
                if widget.__self__ == button:
                    break
            id = id.strip('\"')
            if id == "next_song" and self.next_song_event is None:
                self.next_song_event = Clock.schedule_once(lambda dt: self.play_next_song(self.button_touch), 0.2)
            elif id == "previous_song" and self.previous_song_event is None:
                self.previous_song_event = Clock.schedule_once(lambda dt: self.play_previous_song(self.button_touch), 0.2)


    def play_next_song(self, touch):
        if touch.is_double_tap:
            change_song(transition="skip", direction="forward")
        else:
            change_song(transition="fade", direction="forward")

        Clock.unschedule(self.next_song_event)
        self.next_song_event = None
    

    def play_previous_song(self, touch):
        if touch.is_double_tap:
            change_song(transition="skip", direction="backward")
        else:
            change_song(transition="fade", direction="backward")

        Clock.unschedule(self.previous_song_event)
        self.previous_song_event = None
    

    def pause_music(self):
        if self.audioplayer.status == "idle":
            self.play_music()
        else:
            self.audioplayer.pause_flag = not self.audioplayer.pause_flag

        if self.audioplayer.pause_flag:
            self.ids.pause.background_normal = "./assets/buttons/play_normal.png"
            self.ids.pause.background_down = "./assets/buttons/play_pressed.png"
            Animation.cancel_all(self.ids.song_cover, "size")
            new_size = (self.height * (1/3), min(1/self.song_cover.image_ratio * self.height * (1/3), self.height * (1/3)))
            anim = Animation(size=new_size, duration=0.25, transition="out_back")
            anim.start(self.song_cover)

        else:
            self.ids.pause.background_normal = "./assets/buttons/pause_normal.png"
            self.ids.pause.background_down = "./assets/buttons/pause_pressed.png"
            Animation.cancel_all(self.song_cover, "size")
            new_size = (self.height * (5/12), min(1/self.song_cover.image_ratio * self.height * (5/12), self.height * (5/12)))
            anim = Animation(size=new_size, duration=0.25, transition="out_back")
            anim.start(self.song_cover)


    def begin_seek(self, touch):
        if self.time_slider.collide_point(*touch.pos) and not self.time_slider.disabled:
            self.stop_move = True
            self.update_time_pos = False
            # new_size = (self.time_slider.size[0] * 1.05, self.time_slider.size[1] * 2)
            # new_pos = (self.time_slider.pos[0] - self.time_slider.size[0] * .025, self.height * (1/3) - self.time_slider.size[1])
            # anim = Animation(size=new_size, pos=new_pos, duration=0.1, transition="linear")
            # anim.start(self.time_slider)
            # try:
            #     if self.audio_clock:
            #         Clock.unschedule(self.audio_clock)
            #         self.audio_clock = None
            # except:
            #     pass
    
    def update_seek(self, touch):
        if touch.grab_current == self.time_slider:
            if self.time_slider.value_normalized == 1:
                self.audioplayer.seek_pos = 0
            self.update_time_text(int(self.time_slider.value_normalized * self.audioplayer.song_length))

    def end_seek(self, touch):
        if touch.grab_current == self.time_slider:
            self.stop_move = False
            try:
                self.audioplayer.seek_pos = int(self.time_slider.value_normalized * self.audioplayer.song_length)
                if self.time_slider.value_normalized == 1 and not self.audioplayer.reverse_audio:
                    self.audioplayer.seek_pos = 0
                elif self.time_slider.value_normalized == 0 and self.audioplayer.reverse_audio:
                    self.audioplayer.seek_pos = self.audioplayer.song_length
                self.audioplayer.pos = self.audioplayer.seek_pos
                self.update_time_text(self.audioplayer.pos)
                self.audioplayer.status = "seek"
                self.update_time_pos = True
                # if not self.audio_clock:
                #     self.audio_clock = Clock.schedule_interval(self.update_song_info, 0.05)
            except:
                pass
    
    def reverse(self):
        self.audioplayer.reverse_audio = not self.audioplayer.reverse_audio
        try:
            self.audioplayer.seek_pos = int(self.time_slider.value_normalized * self.audioplayer.song_length)
            self.audioplayer.pos = self.audioplayer.seek_pos
            self.audioplayer.status = "seek"
        except:
            pass
            

    def update_time_text(self, pos):
        pos_time, neg_time = self._format_time(pos/1000/self.audioplayer.speed, self.audioplayer.song_length/1000/self.audioplayer.speed)
        self.ids.song_pos.text = pos_time
        self.ids.neg_song_pos.text = neg_time

    def update_song_info(self, dt): # Main song info update loop

        # Update song position
        if self.update_time_pos:
            self.time_slider.value_normalized = (int(self.audioplayer.pos) / self.audioplayer.song_length)
            self.update_time_text(self.audioplayer.pos)

        
        # Update song name / artist (and position if user is holding the time slider)
        if self.ids.song_name.text != self.audioplayer.song_data["song"]:
            self.ids.song_name.text = self.audioplayer.song_data["song"]
            if not self.update_time_pos:
                self.update_time_text(int(self.time_slider.value_normalized * self.audioplayer.song_length))
        

        # Update song cover
        if self.song_cover.source != self.audioplayer.song_data["cover"]:
            self.song_cover.source = self.audioplayer.song_data["cover"]
            Animation.cancel_all(self.song_cover, "size")
            new_size = (self.height * (5/12), min(1/self.song_cover.image_ratio * self.height * (5/12), self.height * (5/12)))
            anim = Animation(size=new_size, duration=0, transition="linear")
            anim.start(self.song_cover)
            if self.song_cover.source != f"{common_vars.app_folder}/assets/covers/default_cover.png":
                new_colors = self.get_average_color()
            else:
                new_colors = ("060606", "111111")

            self._color_updater = self._generate_background_color((self.c1, self.c2), new_colors)
            self.background_updater = Clock.schedule_interval(self._update_background_color, 0.0333)
            self.c1, self.c2 = new_colors


    def get_average_color(self):

        color_array = np.ndarray(shape=(len(self.song_cover.texture.pixels)//4,4), dtype=np.uint8, buffer=self.song_cover.texture.pixels,  order="C").T
        avg_red = np.mean(color_array[0])
        avg_green = np.mean(color_array[1])
        avg_blue = np.mean(color_array[2])
        return f"{round(avg_red*.2):02x}{round(avg_green*.2):02x}{round(avg_blue*.2):02x}", \
               f"{round(avg_red*.5):02x}{round(avg_green*.5):02x}{round(avg_blue*.5):02x}"

    @staticmethod
    def _generate_background_color(old_color, new_color, *args):
        old_c1 = get_color_from_hex(old_color[0])
        old_c2 = get_color_from_hex(old_color[1])
        new_c1 = get_color_from_hex(new_color[0])
        new_c2 = get_color_from_hex(new_color[1])

        c1_red_transition = np.linspace(old_c1[0], new_c1[0], num=10, endpoint=True)
        c1_green_transition = np.linspace(old_c1[1], new_c1[1], num=10, endpoint=True)
        c1_blue_transition = np.linspace(old_c1[2], new_c1[2], num=10, endpoint=True)

        c2_red_transition = np.linspace(old_c2[0], new_c2[0], num=10, endpoint=True)
        c2_green_transition = np.linspace(old_c2[1], new_c2[1], num=10, endpoint=True)
        c2_blue_transition = np.linspace(old_c2[2], new_c2[2], num=10, endpoint=True)

        for c1, c2 in zip(zip(c1_red_transition, c1_green_transition, c1_blue_transition), zip(c2_red_transition, c2_green_transition, c2_blue_transition)):
            c1 = f"{round(c1[0]*255):02x}{round(c1[1]*255):02x}{round(c1[2]*255):02x}"
            c2 = f"{round(c2[0]*255):02x}{round(c2[1]*255):02x}{round(c2[2]*255):02x}"

            yield c1, c2
        
    def _update_background_color(self, dt):
        try:
            c1, c2 = next(self._color_updater)
        except StopIteration:
            Clock.unschedule(self.background_updater)
        else:
            self.ids.background.texture = Gradient.vertical(get_color_from_hex(c1), get_color_from_hex(c2))


    @staticmethod
    def _format_time(seconds, length):

        seconds = int(seconds)
        length = int(length) - seconds

        #print(seconds)

        if length < 3_600:
            pos_min = seconds // 60
            pos_sec = seconds % 60
            length_min = length // 60
            length_sec = length % 60
            pos = f"{pos_min}:{pos_sec:02d}"
            neg = f"-{length_min}:{length_sec:02d}"
        else:
            pos_hour = seconds // 3600
            pos_min = (seconds % 3600) // 60
            pos_sec = (seconds % 3600) % 60
            length_hour = length // 3600
            length_min = (length % 3600) // 60
            length_sec = (length % 3600) % 60
            pos = f"{pos_hour}:{pos_min:02d}:{pos_sec:02d}"
            neg = f"-{length_hour}:{length_min:02d}:{length_sec:02d}"

        return pos, neg
    
    def stop_time(self):
        self.audioplayer.status = "zawarudo"

class SongsScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.add_widget(SongsDisplay())
    
    # def on_enter(self, *args):
    #     print(self.children[0].song_list.data)
    #     if self.children[0].song_list.data is None:
    #         self.children[0].refresh_songs()
    #         print("adding data...")
    #     else:
    #         print("Not adding data...")

class SongsDisplay(Widget):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.audioplayer = common_vars.audioplayer

        self.song_list = self.ids.song_list
        self.song_search = self.ids.song_search
        self.song_list.data = [{"text" : song_data["song"], "song_filepath" : song_data["file"]} for song_data in self.audioplayer.playlist]


    def on_text(self, widget):

        width = widget.width - widget.padding[0] - widget.padding[2]

        if widget._lines_labels[0].size[0] < width:
            widget.halign = "left"
            widget.scroll_x = 0
        else:
            widget.halign = "right"
        
        self.refresh_songs()
        self.song_list.scroll_y = 1
        
        
        #print(self.song_list.data)
    
    def refresh_songs(self):
        if self.song_search.text == "":
            self.song_list.data = [{"text" : song_data["song"], "song_filepath" : song_data["file"]} for song_data in self.audioplayer.playlist]
            return

        self.song_list.data = [{"text" : song_data["song"], "song_filepath" : song_data["file"]} 
                               for song_data in self.audioplayer.playlist if self.song_search.text.lower() in song_data["song"].lower()]

class SongButton(Button):

    audioplayer = common_vars.audioplayer
    
    def on_parent(self, a, b):
        asset = f"{common_vars.app_folder}/assets/covers/{self.text}"
        if os.path.isfile(f"{asset}.png"):
            self.ids.song_cover.source = f"{asset}.png"
        elif os.path.isfile(f"{asset}.jpg"):
            self.ids.song_cover.source = f"{asset}.jpg"
        else:
            self.ids.song_cover.source = f"{common_vars.app_folder}/assets/covers/default_cover.png"

    def on_release(self, **kwargs):
        if self.audioplayer.status == "idle":
            common_vars.audioplayer.song_data = self.audioplayer.playlist.set_song(self.song_filepath)
            # Counterintuitively, this actually starts the music since audioplayer status is idle
            self.parent.parent.parent.parent.manager.get_screen("main").children[0].pause_music() # Truly cursed
        else:
            change_song(self.song_filepath, transition="fade")
    

class SettingsScreen(Screen):

    def __init__(self, **kw):
        super().__init__(**kw)
        self.add_widget(SettingsDisplay())
    
class SettingsDisplay(Widget):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.audioplayer = common_vars.audioplayer
        self.fps_clock = Clock.schedule_interval(self.update_fps, 0.5)
        self.debug_clock = Clock.schedule_interval(self.get_debug_info, 0.05)
        self.speed_slider = self.ids.speed_slider
        self.fade_slider = self.ids.fade_slider

        if os.path.exists("./config"):
            with open("./config", "rb") as fp:
                fp.seek(-10, 2)
                self.speed_slider.value = struct.unpack("d", fp.read(8))[0] * 100
                self.fade_slider.value = int.from_bytes(fp.read(2), "little")
        else:
            self.speed_slider.value = 100
            self.fade_slider.value = 3000
    
    def change_speed(self, touch):
        if touch.grab_current == self.speed_slider:
            self.audioplayer.speed = (self.speed_slider.value / 100)
    

    def change_fade_duration(self, touch):
        if touch.grab_current == self.fade_slider:
            self.audioplayer.base_fade_duration = self.fade_slider.value
            self.audioplayer.fade_duration = int(self.fade_slider.value * (self.speed_slider.value / 100))

    def update_fps(self, dt):
        fps = Clock.get_fps()
        self.ids.fps.text = f"FPS: {fps:.3f}"
    
    def get_debug_info(self, dt):
        try:
            self.ids.debug_info.text = "Debug Info:" + self.audioplayer.debug_string
        except:
            pass



class DndAudio(App):

    def build(self):
        if platform != "android":
            Window.size = (375, 700)

        sm = ScreenManager(transition=NoTransition())
        sm.add_widget(SongsScreen(name="songs"))
        sm.add_widget(MainScreen(name="main"))
        sm.add_widget(SettingsScreen(name="settings"))
        sm.transition.direction = "right"
        sm.current = "main"
        sm.transition = SlideTransition()
        return sm
        # return MainDisplay()



if __name__ == '__main__':
    DndAudio().run()

    with open("./config", "wb") as fp:
        song_data = common_vars.audioplayer.song_data["file"]
        song_pos = round(common_vars.audioplayer.pos)
        song_length = common_vars.audioplayer.song_length
        total_frames = int(common_vars.audioplayer.total_frames)
        speed = common_vars.audioplayer.speed
        fade_duration = int(common_vars.audioplayer.fade_duration)
        fp.write(len(song_data).to_bytes(2, "little"))
        fp.write(bytes(song_data, encoding="utf-8"))
        fp.write(song_pos.to_bytes(4, "little"))
        fp.write(total_frames.to_bytes(8, "little"))
        fp.write(struct.pack("2d", song_length, speed))
        fp.write(fade_duration.to_bytes(2, "little"))