import AppIntents
import Foundation

@available(iOS 18.0, *)
private enum MagistrateIntentDestination {
  static let voice = URL(string: "magistrate:/voice?autostart=true")!
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
  static var title: LocalizedStringResource = "Start Magistrate"
  static var description = IntentDescription("Open Magistrate to continue your Magi conversation.")
  static let destination = MagistrateIntentDestination.voice
}

@available(iOS 18.0, *)
struct TalkToMagistrateIntent: MagistrateOpeningIntent {
  static var title: LocalizedStringResource = "Talk to Magistrate"
  static var description = IntentDescription("Open foreground Voice Mode and start listening.")
  static let destination = MagistrateIntentDestination.voice
}

@available(iOS 18.0, *)
struct AskMagistrateIntent: MagistrateOpeningIntent {
  static var title: LocalizedStringResource = "Ask Magistrate"
  static var description = IntentDescription("Open foreground Voice Mode to ask Magi.")
  static let destination = MagistrateIntentDestination.voice
}

@available(iOS 18.0, *)
struct WhatsRunningIntent: MagistrateOpeningIntent {
  static var title: LocalizedStringResource = "What's running?"
  static var description = IntentDescription("Ask Magi for the current structured work summary.")
  static let destination = MagistrateIntentDestination.running
}

@available(iOS 18.0, *)
struct WhatNeedsAttentionIntent: MagistrateOpeningIntent {
  static var title: LocalizedStringResource = "What needs attention?"
  static var description = IntentDescription("Open the current Magistrate Attention overview.")
  static let destination = MagistrateIntentDestination.attention
}

@available(iOS 18.0, *)
struct MagistrateVoiceIntent: MagistrateOpeningIntent {
  static var title: LocalizedStringResource = "Magistrate Voice"
  static var description = IntentDescription("Start Magistrate Voice from the Action Button or Shortcuts.")
  static let destination = MagistrateIntentDestination.voice
}

@available(iOS 18.0, *)
struct MagistrateAppShortcuts: AppShortcutsProvider {
  static var appShortcuts: [AppShortcut] {
    AppShortcut(intent: StartMagistrateIntent(), phrases: ["Start \(.applicationName)"], shortTitle: "Start", systemImageName: "triangle")
    AppShortcut(intent: TalkToMagistrateIntent(), phrases: ["Talk to \(.applicationName)"], shortTitle: "Talk", systemImageName: "waveform")
    AppShortcut(intent: AskMagistrateIntent(), phrases: ["Ask \(.applicationName)"], shortTitle: "Ask", systemImageName: "mic")
    AppShortcut(intent: WhatsRunningIntent(), phrases: ["What's running in \(.applicationName)"], shortTitle: "What's running?", systemImageName: "bolt.horizontal")
    AppShortcut(intent: WhatNeedsAttentionIntent(), phrases: ["What needs attention in \(.applicationName)"], shortTitle: "Needs attention", systemImageName: "exclamationmark.circle")
    AppShortcut(intent: MagistrateVoiceIntent(), phrases: ["Open \(.applicationName) Voice"], shortTitle: "Magistrate Voice", systemImageName: "action")
  }
}
