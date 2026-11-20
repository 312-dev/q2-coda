/* u_audio's exported names, renamed in this module's private copy (docs/iphone-mode.md#the-uac1-module): the
 * stock kernel already exports these from Shanling's modified u_audio (f_uac2's), and a module exporting a name
 * the kernel has is refused at load. Renamed, the module's f_uac1 calls only its own u_audio. */
#define u_audio_start_capture q2_u_audio_start_capture
#define u_audio_stop_capture q2_u_audio_stop_capture
#define u_audio_start_playback q2_u_audio_start_playback
#define u_audio_stop_playback q2_u_audio_stop_playback
#define g_audio_setup q2_g_audio_setup
#define g_audio_cleanup q2_g_audio_cleanup
