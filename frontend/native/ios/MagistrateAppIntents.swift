import AppIntents
import Foundation

@available(iOS 18.0, *)
private enum MagistrateIntentDestination {
  // One invocation identity survives duplicate OS URL callbacks, while a later
  // Action Button press remains a new entry (never an execution authorization).
  static var voice: URL {
    URL(string: "magistrate:/voice?autostart=true&entry=\(UUID().uuidString)")!
  }
  static let running = URL(string: "magistrate:/chat?shortcut=running")!
  static let attention = URL(string: "magistrate:/attention?overview=true")!
}

@available(iOS 18.0, *)
protocol MagistrateOpeningIntent: AppIntent {
  static var destination: URL { get }
}

@available(iOS 18.0, *)
extension MagistrateOpeningIntent {
  static var openAppWhenRun: Bool { true }

  @MainActor
  func perform() async throws -> some IntentResult & OpensIntent {
    .result(opensIntent: OpenURLIntent(Self.destination))
  }
}

@available(iOS 18.0, *)
struct StartMagistrateIntent: MagistrateOpeningIntent {
  static var title: LocalizedStringResource = "Open Voice"
  static var description = IntentDescription("Open foreground voice input for your conversation.")
  static var destination: URL { MagistrateIntentDestination.voice }
}

@available(iOS 18.0, *)
struct TalkToMagistrateIntent: MagistrateOpeningIntent {
  static var title: LocalizedStringResource = "Talk"
  static var description = IntentDescription("Open foreground voice input after session restoration and microphone consent.")
  static var destination: URL { MagistrateIntentDestination.voice }
}

@available(iOS 18.0, *)
struct AskMagistrateIntent: MagistrateOpeningIntent {
  static var title: LocalizedStringResource = "Ask"
  static var description = IntentDescription("Open foreground voice input to ask a question.")
  static var destination: URL { MagistrateIntentDestination.voice }
}

@available(iOS 18.0, *)
struct WhatsRunningIntent: MagistrateOpeningIntent {
  static var title: LocalizedStringResource = "Work status"
  static var description = IntentDescription("Open the current structured work summary.")
  static let destination = MagistrateIntentDestination.running
}

@available(iOS 18.0, *)
struct WhatNeedsAttentionIntent: MagistrateOpeningIntent {
  static var title: LocalizedStringResource = "Attention"
  static var description = IntentDescription("Open current items that need your attention.")
  static let destination = MagistrateIntentDestination.attention
}

@available(iOS 18.0, *)
struct MagistrateVoiceIntent: MagistrateOpeningIntent {
  static var title: LocalizedStringResource = "Start Voice"
  static var description = IntentDescription("Open voice input in the foreground. Listening requires a restored session and microphone consent.")
  static var destination: URL { MagistrateIntentDestination.voice }
}

@available(iOS 18.0, *)
struct MagistrateAppShortcuts: AppShortcutsProvider {
  static var appShortcuts: [AppShortcut] {
    AppShortcut(intent: StartMagistrateIntent(), phrases: ["Start \(.applicationName)"], shortTitle: "Open Voice", systemImageName: "triangle")
    AppShortcut(intent: TalkToMagistrateIntent(), phrases: ["Talk to \(.applicationName)"], shortTitle: "Talk", systemImageName: "waveform")
    AppShortcut(intent: AskMagistrateIntent(), phrases: ["Ask \(.applicationName)"], shortTitle: "Ask", systemImageName: "mic")
    AppShortcut(intent: WhatsRunningIntent(), phrases: ["What's running in \(.applicationName)"], shortTitle: "Work status", systemImageName: "bolt.horizontal")
    AppShortcut(intent: WhatNeedsAttentionIntent(), phrases: ["What needs attention in \(.applicationName)"], shortTitle: "Attention", systemImageName: "exclamationmark.circle")
    AppShortcut(intent: MagistrateVoiceIntent(), phrases: ["Start Voice in \(.applicationName)", "Open \(.applicationName) Voice"], shortTitle: "Start Voice", systemImageName: "mic")
  }
}
