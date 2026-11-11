// One-window capture; raw OCR strings go ONLY through a pipe to summarize_ocr.py.
// No image files, activation, permission prompts, or clipboard access.
import Foundation
import AppKit
import CoreGraphics
import ScreenCaptureKit
import Vision

func run() async throws {
    guard CGPreflightScreenCaptureAccess() else {
        print("{\"skipped\":\"screen_recording_denied\"}"); return
    }
    let start = CFAbsoluteTimeGetCurrent()
    let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
    guard let window = content.windows.first(where: {
        $0.owningApplication?.bundleIdentifier == "com.mitchellh.ghostty" && $0.windowLayer == 0 && $0.frame.width > 100
    }) else { print("{\"skipped\":\"no_onscreen_ghostty_window\"}"); return }
    let filter = SCContentFilter(desktopIndependentWindow: window)
    let config = SCStreamConfiguration()
    config.width = Int(window.frame.width * 2)
    config.height = Int(window.frame.height * 2)
    config.showsCursor = false
    let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
    let captureMs = (CFAbsoluteTimeGetCurrent() - start) * 1000
    var results: [[String: Any]] = []
    for (name, level) in [("fast", VNRequestTextRecognitionLevel.fast), ("accurate", VNRequestTextRecognitionLevel.accurate)] {
        for trial in 0..<5 {
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = level
            request.usesLanguageCorrection = false
            let handler = VNImageRequestHandler(cgImage: image, options: [:])
            let t = CFAbsoluteTimeGetCurrent()
            try handler.perform([request])
            let ms = (CFAbsoluteTimeGetCurrent() - t) * 1000
            let observations = request.results ?? []
            let text = observations.compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
            results.append(["mode": name, "trial": trial, "ms": ms, "observations": observations.count, "text": text])
        }
    }
    let data = try JSONSerialization.data(withJSONObject: ["app": "Ghostty", "capture_ms": captureMs, "width": image.width, "height": image.height, "results": results])
    print(String(decoding: data, as: UTF8.self))
}
let app = NSApplication.shared
Task {
    do { try await run(); exit(0) }
    catch { print("{\"error\":\"capture_or_vision_failed\"}"); exit(1) }
}
dispatchMain()
