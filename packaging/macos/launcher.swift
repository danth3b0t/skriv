// Hark.app's main executable (Contents/MacOS/Hark).
//
// It runs the hark client *in this process* by loading the uv-managed CPython (libpython) and
// calling Py_BytesMain with the venv's interpreter path as argv[0], so `python -m skriv_client
// app` runs inside the bundle's own executable:
//
// - TCC (Microphone, Accessibility, Input Monitoring) attributes access to the *responsible*
//   process. Launched from Finder/`open`/a login item, that is this executable (ppid 1), so the
//   grants belong to Hark.app (bundle id + code signature), not to Terminal or python3.12.
// - Started from another process (a shell), it would inherit that process's responsibility;
//   it then re-spawns itself with responsibility disclaimed (the `responsibility_spawnattrs_
//   setdisclaim` SPI Qt Creator and others use), so it is still Hark that asks and is granted.
// - NSBundle.mainBundle is Hark.app, which SMAppService.mainApp (launch at login) and the usage
//   strings in Info.plist (NSMicrophoneUsageDescription) need.
//
// Info.plist key `HarkVenv` (written by scripts/build-macos-app) is the absolute path of the
// virtualenv to run; the env var HARK_VENV overrides it. Python code is not copied into the
// bundle, so editing it never changes the signature (and never invalidates the TCC grants).
// Without a terminal, stdout/stderr go to ~/Library/Logs/hark/app.log.

import Darwin
import Foundation

let disclaimedEnv = "HARK_LAUNCHER_DISCLAIMED"
var childPid: pid_t = 0

func say(_ message: String) {
    FileHandle.standardError.write(Data("Hark: \(message)\n".utf8))
}

func fail(_ message: String) -> Never {
    say(message)
    if isatty(STDERR_FILENO) == 0 {  // launched from Finder: nobody reads the log
        CFUserNotificationDisplayNotice(
            0, kCFUserNotificationStopAlertLevel, nil, nil, nil,
            "Hark cannot start" as CFString, message as CFString, nil)
    }
    exit(1)
}

/// Re-runs this executable as its own responsible process and waits for it. Returns only if the
/// SPI is unavailable (then this process keeps its parent's responsibility).
func runDisclaimed() {
    typealias SetDisclaim = @convention(c) (UnsafeMutablePointer<posix_spawnattr_t?>, Int32) -> Int32
    guard let sym = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_spawnattrs_setdisclaim"),
          let exe = Bundle.main.executablePath
    else {
        say("cannot disclaim responsibility; permissions go to the launching app")
        return
    }
    var attr: posix_spawnattr_t?
    posix_spawnattr_init(&attr)
    defer { posix_spawnattr_destroy(&attr) }
    guard unsafeBitCast(sym, to: SetDisclaim.self)(&attr, 1) == 0 else {
        say("responsibility_spawnattrs_setdisclaim failed; permissions go to the launching app")
        return
    }
    setenv(disclaimedEnv, "1", 1)
    let rc = posix_spawn(&childPid, exe, nil, &attr, CommandLine.unsafeArgv, environ)
    guard rc == 0 else { fail("posix_spawn \(exe): \(String(cString: strerror(rc)))") }
    // Ctrl+C reaches the child through the terminal's process group; forward the rest.
    signal(SIGINT, SIG_IGN)
    for sig in [SIGTERM, SIGHUP, SIGQUIT] {
        signal(sig) { s in kill(childPid, s) }
    }
    var status: Int32 = 0
    while waitpid(childPid, &status, 0) == -1 && errno == EINTR {}
    let low = status & 0x7f
    exit(low == 0 ? (status >> 8) & 0xff : 128 + low)
}

/// key = value lines of pyvenv.cfg
func readVenvConfig(_ venv: String) -> [String: String] {
    guard let text = try? String(contentsOfFile: venv + "/pyvenv.cfg", encoding: .utf8) else {
        fail("no virtualenv at \(venv) (pyvenv.cfg missing); run `uv sync` and scripts/build-macos-app")
    }
    var cfg: [String: String] = [:]
    for line in text.split(separator: "\n") {
        let parts = line.split(separator: "=", maxSplits: 1)
        if parts.count == 2 {
            cfg[parts[0].trimmingCharacters(in: .whitespaces)] =
                parts[1].trimmingCharacters(in: .whitespaces)
        }
    }
    return cfg
}

if getenv(disclaimedEnv) == nil && getppid() != 1 {
    runDisclaimed()
}
unsetenv(disclaimedEnv)

if isatty(STDERR_FILENO) == 0 {
    let logs = NSHomeDirectory() + "/Library/Logs/hark"
    try? FileManager.default.createDirectory(atPath: logs, withIntermediateDirectories: true)
    let fd = open(logs + "/app.log", O_WRONLY | O_CREAT | O_APPEND, 0o600)
    if fd >= 0 {
        dup2(fd, STDOUT_FILENO)
        dup2(fd, STDERR_FILENO)
        close(fd)
    }
}

guard let venv = ProcessInfo.processInfo.environment["HARK_VENV"]
    ?? (Bundle.main.object(forInfoDictionaryKey: "HarkVenv") as? String)
else { fail("Info.plist has no HarkVenv; rebuild with scripts/build-macos-app") }

let cfg = readVenvConfig(venv)
guard let home = cfg["home"], let version = cfg["version_info"] ?? cfg["version"] else {
    fail("\(venv)/pyvenv.cfg has no home/version_info")
}
let majorMinor = version.split(separator: ".").prefix(2).joined(separator: ".")
let libpython = (home as NSString).deletingLastPathComponent + "/lib/libpython\(majorMinor).dylib"
let python = venv + "/bin/python"
guard FileManager.default.isExecutableFile(atPath: python) else { fail("\(python) missing") }
guard let lib = dlopen(libpython, RTLD_NOW | RTLD_GLOBAL) else {
    fail("cannot load \(libpython): \(String(cString: dlerror()))")
}
guard let main = dlsym(lib, "Py_BytesMain") else { fail("\(libpython) has no Py_BytesMain") }
typealias PyBytesMain = @convention(c) (Int32, UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?) -> Int32

// argv[0] = the venv's python: CPython finds pyvenv.cfg next to it, so sys.prefix is the venv
// and sys.executable is a real interpreter (for `hark retry` and other children).
// Launch Services may add -psn_* arguments; drop them.
let extra = CommandLine.arguments.dropFirst().filter { !$0.hasPrefix("-psn_") }
let args = [python, "-m", "skriv_client", "app"] + extra
var cargs: [UnsafeMutablePointer<CChar>?] = args.map { strdup($0) } + [nil]
exit(unsafeBitCast(main, to: PyBytesMain.self)(Int32(args.count), &cargs))
