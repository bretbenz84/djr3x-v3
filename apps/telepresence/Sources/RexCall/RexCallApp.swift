// Rex Call: the telepresence Mac app (docs/TELEPRESENCE_PLAN.md, stage 2).
import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    var model: CallModel?

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    @MainActor func applicationWillTerminate(_ notification: Notification) {
        model?.hangUpBeforeQuit()   // Rex parks rather than waiting 30 s for a reconnect.
    }
}

@main
struct RexCallApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = CallModel()

    var body: some Scene {
        WindowGroup("Rex") {
            ContentView()
                .environmentObject(model)
                .onAppear { delegate.model = model }
        }
        .windowResizability(.contentMinSize)
    }
}
