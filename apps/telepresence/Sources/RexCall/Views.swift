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
            Text("Hear the room, see through his camera and talk through his speaker. His eyes turn magenta while you're on.")
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
        .onAppear { model.refreshDevices() }
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
    @FocusState private var focused: Bool
    @State private var holding = false
    @State private var showAudio = false

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .topLeading) {
                Color.black
                if let image = model.image {
                    Image(decorative: image, scale: 1).resizable().aspectRatio(contentMode: .fit)
                } else {
                    Text(model.videoExpected ? "Waiting for the camera…" : "Rex's camera is off. You can still hear and talk.")
                        .foregroundStyle(.white.opacity(0.7))
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                Text(stats).font(.caption.monospacedDigit()).padding(6)
                    .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 6))
                    .foregroundStyle(.white).padding(10)
            }
            HStack(spacing: 14) {
                Button(action: model.toggleMute) {
                    Label(model.muted ? "Muted" : "Talking", systemImage: model.muted ? "mic.slash.fill" : "mic.fill")
                        .frame(minWidth: 110)
                }
                .controlSize(.large)
                .tint(model.muted ? .gray : .green)
                .buttonStyle(.borderedProminent)
                ProgressView(value: Double(model.level)).frame(width: 80).opacity(model.muted ? 0.3 : 1)
                Text("M to mute or unmute · hold Space to talk").font(.caption).foregroundStyle(.secondary)
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text(model.audioInUse).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    if let note = model.audioNote {
                        Text(note).font(.caption).foregroundStyle(.orange).lineLimit(2)
                    } else if !model.voiceProcessing {
                        Text("No echo cancelling: use headphones").font(.caption).foregroundStyle(.orange)
                    }
                }
                Button { showAudio.toggle() } label: { Label("Audio", systemImage: "slider.horizontal.3") }
                    .controlSize(.large)
                    .popover(isPresented: $showAudio) {
                        AudioSettings().padding().frame(width: 460).onAppear { model.refreshDevices() }
                    }
                Button(role: .destructive, action: model.hangUp) { Label("Hang Up", systemImage: "phone.down.fill") }
                    .controlSize(.large).buttonStyle(.borderedProminent).tint(.red)
                    .keyboardShortcut(.cancelAction)
            }
            .padding(12)
        }
        .focusable()
        .focusEffectDisabled()
        .focused($focused)
        .onAppear { focused = true }
        .onKeyPress("m") { model.toggleMute(); return .handled }
        .onKeyPress(.space, phases: [.down, .up]) { press in
            if press.phase == .down, !holding, model.muted { holding = true; model.muted = false }
            if press.phase == .up, holding { holding = false; model.muted = true }
            return .handled
        }
        .frame(minWidth: 640, minHeight: 440)
    }

    var stats: String {
        func ms(_ v: Double?) -> String { v.map { "\(Int($0.rounded())) ms" } ?? "–" }
        var parts = ["round trip \(ms(model.rttMs))"]
        if model.videoExpected { parts.append("picture \(ms(model.videoAgeMs)) old, \(Int(model.fps)) fps") }
        if !model.robotLine.isEmpty { parts.append(model.robotLine) }
        return parts.joined(separator: " · ")
    }
}
