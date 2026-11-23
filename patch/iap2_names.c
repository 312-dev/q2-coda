/* iAP2's log lines (docs/iphone-mode.md#iap2): each message's name, bytes in hex, a line through the host's log, and a
 * message either way with its name and parameters. Part of the PodBox port (iap2.h). */
#include "q2_iap_sys.h"
#include "iap2.h"
#include "iap2_names.h"

static const struct {
    u16 id;
    const char *name;
} iap2_names[] = {
    { 0x0B00, "StartBluetoothPairing" }, { 0x0B01, "BluetoothPairingAccessoryInformation" },
    { 0x0B02, "BluetoothPairingStatus" }, { 0x0B03, "StopBluetoothPairing" },
    { 0x1D00, "StartIdentification" }, { 0x1D01, "IdentificationInformation" },
    { 0x1D02, "IdentificationAccepted" }, { 0x1D03, "IdentificationRejected" },
    { 0x1D05, "CancelIdentification" }, { 0x1D06, "IdentificationInformationUpdate" },
    { 0x4154, "StartCallStateUpdates" }, { 0x4156, "StopCallStateUpdates" },
    { 0x4157, "StartCommunicationsUpdates" }, { 0x4159, "StopCommunicationsUpdates" },
    { 0x4300, "CarPlayAvailability" }, { 0x4301, "CarPlayStartSession" },
    { 0x4C00, "StartMediaLibraryInformation" }, { 0x4C01, "MediaLibraryInformation" },
    { 0x4C02, "StopMediaLibraryInformation" }, { 0x4C03, "StartMediaLibraryUpdates" },
    { 0x4C04, "MediaLibraryUpdate" }, { 0x4C05, "StopMediaLibraryUpdates" },
    { 0x4C06, "PlayMediaLibraryCurrentSelection" }, { 0x4C07, "PlayMediaLibraryItems" },
    { 0x4C08, "PlayMediaLibraryCollection" }, { 0x4C09, "PlayMediaLibrarySpecial" },
    { 0x4E01, "BluetoothComponentInformation" }, { 0x4E03, "StartBluetoothConnectionUpdates" },
    { 0x4E04, "BluetoothConnectionUpdate" }, { 0x4E05, "StopBluetoothConnectionUpdates" },
    { 0x4E09, "DeviceInformationUpdate" }, { 0x4E0A, "DeviceLanguageUpdate" }, { 0x4E0B, "DeviceTimeUpdate" },
    { 0x4E0C, "DeviceUUIDUpdate" }, { 0x4E0E, "DeviceTransportIdentifierNotification" },
    { 0x5000, "StartNowPlayingUpdates" }, { 0x5001, "NowPlayingUpdate" }, { 0x5002, "StopNowPlayingUpdates" },
    { 0x5003, "SetNowPlayingInformation" }, { 0x5200, "StartRouteGuidanceUpdates" },
    { 0x5203, "StopRouteGuidanceUpdates" }, { 0x6800, "StartHID" }, { 0x6801, "DeviceHIDReport" },
    { 0x6802, "AccessoryHIDReport" }, { 0x6803, "StopHID" }, { 0x6806, "StartNativeHID" },
    { 0x6807, "HIDComponentUpdate" }, { 0xAA00, "RequestAuthenticationCertificate" },
    { 0xAA01, "AuthenticationCertificate" }, { 0xAA02, "RequestAuthenticationChallengeResponse" },
    { 0xAA03, "AuthenticationResponse" }, { 0xAA04, "AuthenticationFailed" },
    { 0xAA05, "AuthenticationSucceeded" }, { 0xAA06, "AccessoryAuthenticationSerialNumber" },
    { 0xAE00, "StartPowerUpdates" }, { 0xAE01, "PowerUpdate" }, { 0xAE02, "StopPowerUpdates" },
    { 0xAE03, "PowerSourceUpdate" }, { 0xAE05, "AccessoryPowerUpdate" },
    { 0xDA00, "StartUSBDeviceModeAudio" }, { 0xDA01, "USBDeviceModeAudioInformation" },
    { 0xDA02, "StopUSBDeviceModeAudio" }, { 0xEA00, "StartExternalAccessoryProtocolSession" },
    { 0xEA01, "StopExternalAccessoryProtocolSession" }, { 0xEA02, "RequestAppLaunch" },
    { 0xFFFA, "StartLocationInformation" }, { 0xFFFB, "LocationInformation" }, { 0xFFFC, "StopLocationInformation" },
};

const char *iap2_name(unsigned id) {
    for (unsigned i = 0; i < sizeof iap2_names / sizeof *iap2_names; ++i)
        if (iap2_names[i].id == id) return iap2_names[i].name;
    return "?";
}

void iap2_hex(char *out, unsigned cap, const u8 *p, unsigned n) {
    static const char d[] = "0123456789abcdef";
    unsigned o = 0, i = 0;
    for (; i < n && o + 6 < cap; ++i) {
        out[o++] = d[p[i] >> 4], out[o++] = d[p[i] & 15];
        if (i + 1 < n) out[o++] = ' ';
    }
    if (i < n) out[o++] = '.', out[o++] = '.'; /* cut: o + 6 < cap left room */
    out[o] = 0;
}

void iap2_log(const char *fmt_label, const char *detail) {
    char line[2600];
    snprintf(line, sizeof line, "%s%s%s", fmt_label, detail && *detail ? " " : "", detail ? detail : "");
    H->log(line);
}

/* A message each way, its name and up to max bytes of its parameters in hex. */
void log_msg(int from_q2, const u8 *m, unsigned len, unsigned max) {
    unsigned id = m[4] << 8 | m[5];
    char h[1200], head[160];
    iap2_hex(h, sizeof h, m + 6, len - 6 < max ? len - 6 : max);
    snprintf(head, sizeof head, "%s %04x %s (%u bytes)", from_q2 ? "msg >" : "msg <", id, iap2_name(id), len);
    iap2_log(head, h);
}
