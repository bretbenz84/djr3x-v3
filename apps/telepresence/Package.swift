// swift-tools-version:6.0
// Rex Call: the Mac app for telepresence (docs/TELEPRESENCE_PLAN.md, stage 2).
// Build the app with ./build.sh; `swift test` runs the core's checks.
import PackageDescription

let package = Package(
    name: "RexCall",
    platforms: [.macOS(.v14)],
    targets: [
        // The wire format, sign-in, connection and jitter buffer: no UI, no audio devices.
        .target(name: "RexCallCore"),
        // This Mac's sound for a call: device choice, echo cancelling, the room's jitter buffer.
        .target(name: "RexCallAudio", dependencies: ["RexCallCore"]),
        // The app: connect, see, hear, talk, hang up.
        .executableTarget(name: "RexCall", dependencies: ["RexCallCore", "RexCallAudio"]),
        // Tries the call's sound on chosen devices with no robot: `rexcall-audio-check --list`.
        .executableTarget(name: "rexcall-audio-check", dependencies: ["RexCallCore", "RexCallAudio"]),
        // A headless caller for end-to-end checks against tools/telepresence_fake_robot.py.
        .executableTarget(name: "rexcall-probe", dependencies: ["RexCallCore"]),
        .testTarget(name: "RexCallCoreTests", dependencies: ["RexCallCore"]),
    ],
    swiftLanguageModes: [.v5]
)
