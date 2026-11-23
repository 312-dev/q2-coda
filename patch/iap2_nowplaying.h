/* Now Playing on iDrive (iap2_nowplaying.c) */
#ifndef IAP2_NOWPLAYING_H
#define IAP2_NOWPLAYING_H
#include "iap2.h"

/* the media item and playback attributes NowPlayingUpdate carries, and those sent when the car names none */
#define MI_PERSISTENT_ID 0x00
#define MI_TITLE 0x01
#define MI_DURATION 0x04
#define MI_ALBUM 0x06
#define MI_ARTIST 0x0C
#define MI_LIKE_SUPPORTED 0x15 /* 0x15 to 0x18: like and ban, bools */
#define MI_BANNED 0x18
#define MI_ARTWORK 0x1A
#define MI_CHAPTER_COUNT 0x1B
#define PB_CHAPTER_INDEX 0x04
#define PB_LIBRARY 0x08
#define PB_STATE 0x00
#define PB_POSITION 0x01
#define PB_QUEUE_INDEX 0x02
#define PB_QUEUE_COUNT 0x03
#define PB_SHUFFLE 0x05
#define PB_REPEAT 0x06
#define PB_APP_NAME 0x07
#define PB_SPEED 0x0C
#define PB_SEEKABLE 0x0D
#define PB_QUEUE_LIST 0x0E    /* 1: the queue's list follows (ql_announce) */
#define PB_APP_BUNDLE 0x10
#define PB_QUEUE_LIST_ID 0x11 /* empty, with PB_QUEUE_LIST 1 */
#define DEFAULT_MEDIA (1u << MI_PERSISTENT_ID | 1u << MI_TITLE | 1u << MI_DURATION | 1u << MI_ALBUM | 1u << MI_ARTIST)
#define DEFAULT_PLAYBACK (1u << PB_STATE | 1u << PB_POSITION | 1u << PB_QUEUE_INDEX | 1u << PB_QUEUE_COUNT)

void poll_now_playing(long now);
void iap2_art_slow(void);
void iap2_art_done(int found);

#endif
