__version__ = "1.4.0"

__changelog__ = {
    "1.4.0": [
        "Host session settings modal: sliders for join voting and start voting enabled",
        "Host can lock/unlock voting from the game UI (only when no votes cast)",
        "Locked voting: cards disabled with lock indicator for all users",
        "Post-reveal lock scheduling: host can schedule lock change for next round",
        "Toggle button disabled during countdown and while votes are being cast",
        "Host vote decision now scoped per-session to prevent modal skip across sessions",
    ],
    "1.3.2": [
        "Fix rate limiting: reduce create session cooldown from 10s to 3s",
        "Fix rate limiting: increase new round limit from 3/hour to 30/hour",
        "Dependencies: update all packages to latest, remove unused shortuuid",
    ],
    "1.3.1": [
        "Version tooltip now shows last 2 versions' changelogs",
        "Fixed changelog not appearing on game page",
    ],
    "1.3.0": [
        "Koningsdag theme: crown decoration, falling Dutch flags, orange color scheme",
        "Audit logging with file rotation (5MB, 3 backups)",
        "Username persistence via localStorage with modal prefill",
        "Deck presets: Fibonacci, Hours, T-shirt sizes with host-only switching",
        "Host-left notification: overlay when host disconnects",
        "Version tooltip: hover version badge to see changelog",
        "Security: crypto-secure client IDs, removed CSP unsafe-inline",
        "Security: proxy-aware IP detection, timezone-aware rate limiting",
        "Bug fixes: session ID message, noscript text, outlier colors, default deck",
    ],
    "1.2.0": [
        "Christmas theme with configurable decorations and snow particles",
        "Monitoring endpoints and Socket.IO rate limiting",
        "Performance and accessibility improvements",
    ],
    "1.1.0": [
        "16-character session IDs with rate limiting",
        "Input validation and resource limits",
    ],
}