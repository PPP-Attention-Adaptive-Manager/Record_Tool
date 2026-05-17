// KWin script: forwards window activation events to our DBus service.
// Loaded at runtime by the KDE Wayland app tracker backend.
// No cleanup needed on unload — callDBus to a non-existent service is a no-op.

const COGNITIVE_SERVICE = "org.cognitive_system.agent";
const COGNITIVE_PATH = "/AppTracker";
const COGNITIVE_INTERFACE = "org.cognitive_system.AppTracker";

workspace.windowActivated.connect(function(client) {
    if (!client) {
        callDBus(
            COGNITIVE_SERVICE,
            COGNITIVE_PATH,
            COGNITIVE_INTERFACE,
            "WindowActivated",
            "",
            "",
            0
        );
        return;
    }

    var resourceClass = client.resourceClass || "";
    var caption = client.caption || "";
    var pid = client.pid || 0;

    callDBus(
        COGNITIVE_SERVICE,
        COGNITIVE_PATH,
        COGNITIVE_INTERFACE,
        "WindowActivated",
        resourceClass,
        caption,
        pid
    );
});
