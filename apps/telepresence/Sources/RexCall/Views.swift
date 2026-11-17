import RexCallAudio
import RexCallCore
import SwiftUI

struct ContentView: View {
    @EnvironmentObject var model: CallModel

    var body: some View {
        switch model.phase {
        case .inCall: CallView()
        default: ConnectView()
        }
    }
}

struct ConnectView: View {
    @EnvironmentObject var model: CallModel
    @State private var token = ""
    @State private var editingToken = false
    @State private var tokenRejected = false

    var connecting: Bool { if case .connecting = model.phase { return true } else { return false } }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Call Rex").font(.largeTitle.bold())
            Text("Hear the room, see through his camera, talk through his speaker, aim his head and drive him. His eyes turn magenta while you're on. Calls start only while he's asleep.")
                .foregroundStyle(.secondary)
            Form {
                TextField("Address", text: $model.address, prompt: Text("192.168.1.50:47800"))
                    .disabled(connecting)
                if !model.hasToken || editingToken {
                    SecureField("Token", text: $token, prompt: Text("telepresence_token from the robot"))
                    HStack {
                        Button("Save token") {
                            tokenRejected = !model.saveToken(token)
                            if !tokenRejected { token = ""; editingToken = false }
                        }
                        if tokenRejected { Text("At least 32 characters, no spaces.").foregroundStyle(.red) }
                    }
                } else {
                    LabeledContent("Token") {
                        HStack { Text("Saved in the keychain"); Button("Change") { editingToken = true } }
                    }
                }
            }
            AudioSettings()
            HStack {
                Button(connecting ? "Calling…" : "Call") { model.connect() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(connecting || !model.hasToken || model.address.isEmpty)
                if case .connecting(let what) = model.phase {
                    ProgressView().controlSize(.small)
                    Text(what).foregroundStyle(.secondary)
                    Button("Cancel") { model.hangUp() }
                }
            }
            if !model.supervisorLine.isEmpty {
                HStack(alignment: .firstTextBaseline) {
                    Text(model.supervisorLine).font(.callout).foregroundStyle(.secondary)
                    if model.supervisorState == "telepresence" && !connecting {
                        Button("End that call") { model.endRunningCall() }.controlSize(.small)
                    }
                }
            }
            if case .ended(let reason) = model.phase {
                Text(CallModel.explain(reason)).foregroundStyle(reason == "hung_up" ? Color.secondary : Color.red)
                if reason == "audio_failed", let note = model.audioNote {
                    Text(note).font(.callout).foregroundStyle(.red).textSelection(.enabled)
                }
            }
            Spacer()
        }
        .padding(24)
        .frame(minWidth: 520, minHeight: 440)
        .onAppear { model.refreshDevices(); model.watchSupervisor(true) }
        .onDisappear { model.watchSupervisor(false) }
        .onChange(of: model.address) { model.refreshSupervisor() }
    }
}

/// Microphone, speaker and echo cancelling; the same controls before and during a call.
struct AudioSettings: View {
    @EnvironmentObject var model: CallModel

    var body: some View {
        Form {
            Picker("Microphone", selection: $model.audioChoice.inputUID) {
                Text("System default (\(model.defaultName(input: true)))").tag(String?.none)
                ForEach(model.inputs) { device in Text(device.name).tag(Optional(device.uid)) }
            }
            Picker("Speaker", selection: $model.audioChoice.outputUID) {
                Text("System default (\(model.defaultName(input: false)))").tag(String?.none)
                ForEach(model.outputs) { device in Text(device.name).tag(Optional(device.uid)) }
            }
            Toggle("Echo cancelling, so speakers work", isOn: $model.audioChoice.echoCancelling)
                .disabled(!model.audioChoice.canCancelEcho)
            if !model.audioChoice.canCancelEcho {
                Text("Echo cancelling works only with both on System default (macOS's limit). Use headphones with these devices, or pick them in System Settings → Sound.")
                    .font(.caption).foregroundStyle(.secondary)
            } else if !model.audioChoice.echoCancelling {
                Text("Without echo cancelling, use headphones so Rex doesn't hear the room twice.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Spacer()
                Button("Refresh devices") { model.refreshDevices() }.controlSize(.small)
            }
        }
    }
}

struct CallView: View {
    @EnvironmentObject var model: CallModel
    @State private var showAudio = false
    @State private var showHead = false
    @State private var showKeys = false
    @State private var monitor: Any?
    @State private var dragStart: HeadAim?

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .topLeading) {
                Color.black
                GeometryReader { geometry in
                    ZStack {
                        if let image = model.image {
                            Image(decorative: image, scale: 1).resizable().aspectRatio(contentMode: .fit)
                        } else {
                            Text(model.videoExpected ? "Waiting for the camera…" : "Rex's camera is off. You can still hear and talk.")
                                .foregroundStyle(.white.opacity(0.7))
                        }
                    }
                    .frame(width: geometry.size.width, height: geometry.size.height)
                    .contentShape(Rectangle())
                    .gesture(DragGesture(minimumDistance: 2).onChanged { drag in
                        guard model.headOffered else { return }
                        let start = dragStart ?? model.aim
                        dragStart = start
                        model.dragging = true
                        model.aimBy(dx: drag.translation.width / max(1, geometry.size.width),
                                    dy: drag.translation.height / max(1, geometry.size.height), from: start)
                    }.onEnded { _ in dragStart = nil; model.dragging = false })
                }
                VStack(alignment: .leading, spacing: 4) {
                    strip(stats)
                    if !model.baseLine.isEmpty { strip(model.baseLine) }
                    if model.driveOffered { strip(model.holdLine ?? model.driveLine, warn: model.holdLine != nil) }
                    if let note = model.headNote { strip(note, warn: true) }
                }
                .padding(10)
            }
            HStack(spacing: 12) {
                Button(action: model.toggleMute) {
                    Label(model.muted ? "Muted" : "Talking", systemImage: model.muted ? "mic.slash.fill" : "mic.fill")
                        .frame(minWidth: 96)
                }
                .controlSize(.large)
                .tint(model.muted ? .gray : .green)
                .buttonStyle(.borderedProminent)
                ProgressView(value: Double(model.level)).frame(width: 60).opacity(model.muted ? 0.3 : 1)
                if model.driveOffered {
                    Button(action: model.stopWheels) { Label("Stop", systemImage: "stop.circle.fill") }
                        .controlSize(.large).buttonStyle(.borderedProminent).tint(.orange)
                        .help("Space: the wheels stop and stay stopped until you let go of the keys")
                    VStack(alignment: .leading, spacing: 0) {
                        Slider(value: $model.speed, in: 0.2...1).frame(width: 110)
                        Text("Speed \(Int(model.speed * 100))% of \(model.maxSpeed)").font(.caption2).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text(model.audioInUse).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    if let note = model.audioNote {
                        Text(note).font(.caption).foregroundStyle(.orange).lineLimit(2)
                    } else if !model.voiceProcessing {
                        Text("No echo cancelling: use headphones").font(.caption).foregroundStyle(.orange)
                    }
                }
                if model.headOffered {
                    Button { showHead.toggle() } label: { Label("Head", systemImage: "eye") }
                        .controlSize(.large)
                        .popover(isPresented: $showHead) { HeadPanel().padding().frame(width: 320) }
                }
                Button { showAudio.toggle() } label: { Label("Audio", systemImage: "slider.horizontal.3") }
                    .controlSize(.large)
                    .popover(isPresented: $showAudio) {
                        AudioSettings().padding().frame(width: 460).onAppear { model.refreshDevices() }
                    }
                Button { showKeys.toggle() } label: { Image(systemName: "keyboard") }
                    .controlSize(.large).help("Keys")
                    .popover(isPresented: $showKeys) { KeysHelp().padding().frame(width: 330) }
                Button(role: .destructive, action: model.hangUp) { Label("Hang Up", systemImage: "phone.down.fill") }
                    .controlSize(.large).buttonStyle(.borderedProminent).tint(.red)
                    .keyboardShortcut(.cancelAction)
            }
            .padding(12)
        }
        .frame(minWidth: 820, minHeight: 520)
        .onAppear { installKeys() }
        .onDisappear { removeKeys() }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didResignKeyNotification)) { _ in model.releaseAll() }
    }

    func strip(_ text: String, warn: Bool = false) -> some View {
        Text(text).font(.caption.monospacedDigit()).padding(6)
            .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 6))
            .foregroundStyle(warn ? Color.orange : Color.white)
    }

    var stats: String {
        func ms(_ v: Double?) -> String { v.map { "\(Int($0.rounded())) ms" } ?? "–" }
        var parts = ["round trip \(ms(model.rttMs))"]
        if model.videoExpected { parts.append("picture \(ms(model.videoAgeMs)) old, \(Int(model.fps)) fps") }
        if !model.robotLine.isEmpty { parts.append(model.robotLine) }
        if let pad = model.controllerName { parts.append("controller: \(pad)") }
        return parts.joined(separator: " · ")
    }

    /// Keys reach the call wherever focus is in the window; with ⌘ held they are left to the menus.
    func installKeys() {
        guard monitor == nil else { return }
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp]) { event in
            if event.modifierFlags.contains(.command) { return event }
            let names: [UInt16: String] = [123: "left", 124: "right", 125: "down", 126: "up", 49: "space"]
            let name = names[event.keyCode] ?? (event.charactersIgnoringModifiers ?? "").lowercased()
            let handled = MainActor.assumeIsolated {
                model.key(name, down: event.type == .keyDown, repeated: event.isARepeat)
            }
            return handled ? nil : event
        }
    }

    func removeKeys() {
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
        model.releaseAll()
    }
}

/// The head's five parts on the robot's scale, and a way home.
struct HeadPanel: View {
    @EnvironmentObject var model: CallModel
    let names = ["neck": "Turn (left … right)", "pitch": "Look (down … up)", "tilt": "Cock (left … right)",
                 "lift": "Lift (low … high)", "visor": "Visor (shut … open)"]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(HeadAim.fields, id: \.self) { field in
                VStack(alignment: .leading, spacing: 0) {
                    Text(names[field] ?? field).font(.caption)
                    Slider(value: Binding(get: { model.aim[field] }, set: { model.aim[field] = $0 }), in: HeadAim.range(field))
                }
            }
            HStack {
                Button("Centre (C)", action: model.centerHead)
                Button("Visor (V)", action: model.toggleVisor)
            }
            Text("Or drag the picture: right looks right, up looks up. The head stays level while the wheels move.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct KeysHelp: View {
    let rows = [("W A S D", "drive (hold)"), ("Space", "stop the wheels"), ("Arrows", "turn and tilt the head up/down"),
                ("Q / E", "cock the head"), ("R / F", "raise / lower the head"), ("C", "centre the head"),
                ("V", "visor open / shut"), ("M", "mute / unmute"), ("T (hold)", "talk"), ("Esc", "hang up"),
                ("Controller", "left stick drives, right stick aims, B stops, Y centres, X visor, A talks")]

    var body: some View {
        Grid(alignment: .leading, verticalSpacing: 4) {
            ForEach(rows, id: \.0) { row in
                GridRow { Text(row.0).bold(); Text(row.1).foregroundStyle(.secondary) }
            }
        }
        .font(.callout)
    }
}
