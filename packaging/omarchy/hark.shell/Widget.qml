import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

// hark's bar glyph and quick panel (design/SPEC.md §5, §6). Facts come from the daemon's
// control socket (`subscribe`, docs/protocol/ctl-v1.md), which never carries dictated text;
// the last dictation's text is read from `hark history -n 1 --json` when the panel opens and
// is never logged. Actions run hark's CLI. Labels and rules live in Model.js.
//
// UNTESTED on hardware: written against Omarchy 4 (quattro) shell sources; see docs/linux.md.
Panel {
  id: root
  moduleName: "hark.shell"
  ipcTarget: "hark.shell"
  manageIpc: false

  // ---- facts
  property var status: null
  property double lastLineAt: 0
  property double now: Date.now()
  property string seenId: ""  // the last dictation shown in the panel (clears attention)
  property var record: null  // newest history record, read when the panel opens
  property string cursor: "last"
  property string wantCursor: ""  // hark-panel: "last"; kept while the panel is open
  property bool cursorMoved: false  // by a key or the pointer since the panel opened
  property var pendingArgv: null  // runs once the panel has closed and focus is back

  readonly property bool online: Model.isOnline(status, lastLineAt, now)
  readonly property string glyphState: Model.glyphState(status, online)
  readonly property var problem: Model.problem(status, online)
  readonly property bool attention: Model.attention(status, online, seenId)
  readonly property string sentence: Model.sentence(status, online, seenId)
  readonly property var last: online ? Model.lastView(record, status, now) : null
  readonly property var rowList: Model.rows(online, problem !== null, last !== null)
  readonly property bool paused: online && !!status && !!status.paused

  // ---- theme: popups surface, inks solved to contrast from colors.toml (Model.tokens)
  property var themeColors: ({})
  readonly property color foreground: Commons.Color.popups.text
  readonly property var tok: Model.tokens(themeColors, hexOf(Commons.Color.popups.background),
    hexOf(foreground), hexOf(Commons.Color.accent))
  readonly property color text2: tok.text2
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  function hexOf(c) {
    function two(v) { var n = Math.round(v * 255); return (n < 16 ? "0" : "") + n.toString(16) }
    return "#" + two(c.r) + two(c.g) + two(c.b)
  }

  function toneColor(tone) {
    if (tone === "live") return tok.live
    if (tone === "warn") return tok.warn
    if (tone === "ok") return tok.ok
    if (tone === "dim") return text2
    return foreground
  }

  function markColor() {
    if (glyphState === "recording") return tok.live
    if (glyphState === "working") return tok.accentInk
    return barForeground
  }

  // ---- actions (hark's CLI through a login shell, so ~/.local/bin is on PATH)
  function run(argv) { Util.execArgv(argv) }

  function afterClose(argv) {
    pendingArgv = argv
    close()
    afterCloseTimer.restart()
  }

  function copyLast() { run(["hark", "last", "--copy"]); close() }
  function pasteLast() { afterClose(["hark", "ctl", "paste-last"]) }
  function retryLast() { if (last && last.canRetry) afterClose(["hark", "retry", "last"]) }
  function togglePause() { if (online) run(["hark", "ctl", paused ? "resume" : "pause"]) }
  function editConfig() {
    var base = Quickshell.env("XDG_CONFIG_HOME") || (Quickshell.env("HOME") + "/.config")
    run(["omarchy-launch-editor", base + "/hark/config.toml"])
    close()
  }
  function doctor() {
    run(["omarchy-launch-floating-terminal-with-presentation", "hark doctor"])
    close()
  }
  function fixProblem() {
    if (!problem) return
    var argv = Model.problemCommand(problem.kind)
    if (argv === null) doctor()
    else run(argv)
  }

  function activate(row) {
    if (row === "pause") togglePause()
    else if (row === "problem") fixProblem()
    else if (row === "last" && last) {
      if (last.enterVerb === "copy") copyLast()
      else if (last.enterVerb === "retry") retryLast()
    }
    else if (row === "config") editConfig()
    else if (row === "doctor") doctor()
  }

  function placeCursor() {
    if (!cursorMoved) cursor = Model.initialRow(rowList, wantCursor)
  }

  function moveCursor(dx, dy) {
    cursorMoved = true
    var footer = cursor === "config" || cursor === "doctor"
    var delta = dy !== 0 ? dy : (footer ? dx : 0)
    if (delta !== 0) cursor = Model.moveRow(rowList, cursor, delta)
  }

  function openAt(want) {
    wantCursor = want
    if (opened) placeCursor()
    else open()
  }

  onOpenedChanged: {
    if (!opened) {
      wantCursor = ""
      return
    }
    seenId = Model.lastId(status)
    cursorMoved = false
    placeCursor()  // again once the history read is in
    readHistory()
    Qt.callLater(function() { if (panel.loadedContent) panel.loadedContent.forceActiveFocus() })
  }
  onRowListChanged: if (rowList.indexOf(cursor) < 0) cursor = Model.initialRow(rowList, "")
  onStatusChanged: {
    var id = Model.lastId(status)
    if (opened && id !== seenId) {
      seenId = id
      readHistory()
    }
  }

  function readHistory() {
    if (!historyProc.running) historyProc.running = true
  }

  ShellIpc {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    // hark-panel (Super Alt D): open with the cursor on the last dictation, or close
    function toggleAt(cursor: string): void { if (root.opened) root.close(); else root.openAt(cursor) }
    function status(): string { return root.sentence }
  }

  // ---- the daemon: one subscribe connection, reconnected every 3 s while it is away
  Socket {
    id: sock
    path: Model.socketPath({
      HARK_CTL_SOCKET: Quickshell.env("HARK_CTL_SOCKET"),
      XDG_RUNTIME_DIR: Quickshell.env("XDG_RUNTIME_DIR"),
      UID: Quickshell.env("UID")
    })
    onConnectedChanged: {
      if (connected) {
        write(Model.subscribeRequest())
        flush()
      } else {
        root.status = null
      }
    }
    parser: SplitParser {
      onRead: function(line) {
        var s = Model.parseStatus(line)
        if (s === null) return
        root.lastLineAt = Date.now()
        root.now = root.lastLineAt
        root.status = s
      }
    }
  }

  Timer {
    interval: 3000
    running: !sock.connected
    repeat: true
    triggeredOnStart: true
    onTriggered: sock.connected = true
  }

  Timer {  // ages, and a daemon that stopped answering without closing the socket
    interval: 5000
    running: true
    repeat: true
    onTriggered: {
      root.now = Date.now()
      if (sock.connected && root.now - root.lastLineAt > Model.OFF_AFTER_MS) sock.connected = false
    }
  }

  Timer {
    id: afterCloseTimer
    interval: 250  // the panel's keyboard focus goes back to the window that had it
    onTriggered: {
      if (root.pendingArgv) root.run(root.pendingArgv)
      root.pendingArgv = null
    }
  }

  Process {
    id: historyProc
    command: ["bash", "-lc", "exec hark history -n 1 --json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.record = Model.parseHistory(text)
        if (root.opened) root.placeCursor()
      }
    }
  }

  FileView {
    id: colorsFile
    path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"
    printErrors: false
    onLoaded: root.themeColors = Model.parseColors(text())
    onLoadFailed: root.themeColors = ({})
  }

  Connections {  // a theme switch arrives over IPC; its colors.toml is in place by then
    target: Commons.Color
    function onBackgroundChanged() { colorsFile.reload() }
    function onAccentChanged() { colorsFile.reload() }
  }

  // ---- the bar glyph
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.glyphState === "paused" ? Model.glyphText("mic-off") : ""
    foreground: root.glyphState === "paused" ? Qt.darker(root.barForeground, 1.55) : root.barForeground
    iconComponent: root.glyphState === "paused" ? null : markComponent
    tooltipText: root.sentence
    Accessible.role: Accessible.StatusBar
    Accessible.name: root.sentence
    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.run(["hark", "ctl", "paste-last"])
      else if (b === Qt.LeftButton) root.toggle()
    }

    Rectangle {  // attention: a 4 px dot, cleared when the panel opens
      visible: root.attention
      width: Style.space(4)
      height: width
      radius: width / 2
      color: root.tok.live
      x: parent.width / 2 + Style.space(5)
      y: parent.height / 2 - Style.space(7)
    }
  }

  Component {
    id: markComponent
    Item {
      Mark {
        anchors.centerIn: parent
        size: button.iconSize
        color: root.markColor()
        opacity: root.glyphState === "off" ? 0.4 : 1
      }
    }
  }

  // ---- the quick panel
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(300))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(480))

    content: PanelKeyCatcher {
      implicitHeight: column.implicitHeight
      anchors.fill: parent
      onMoveRequested: function(dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: root.activate(root.cursor)
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if ((t === "p" || t === "P") && root.last && root.last.canPaste) root.pasteLast()
        else if (t === "r" || t === "R") root.retryLast()
      }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(18)

        // header: mark, hark, server · rtt, and the Pause / Resume text control
        RowLayout {
          width: parent.width
          spacing: Style.space(8)

          Mark {
            size: Style.font.icon
            color: root.foreground
            opacity: root.online && !root.paused ? 1 : 0.4
            Layout.alignment: Qt.AlignVCenter
          }
          Text {
            text: "hark"
            textFormat: Text.PlainText
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            font.bold: true
          }
          Repeater {
            model: Model.serverParts(root.status, root.online)
            Text {
              required property var modelData
              required property int index
              text: (index > 0 ? "· " : "") + modelData.text
              textFormat: Text.PlainText
              color: modelData.warn ? root.tok.warn : root.text2
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }
          }
          Item { Layout.fillWidth: true; implicitHeight: 1 }
          CursorSurface {
            visible: root.online
            hasCursor: root.cursor === "pause"
            foreground: root.foreground
            implicitWidth: pauseText.implicitWidth + Style.space(16)
            implicitHeight: Style.space(28)
            Accessible.role: Accessible.Button
            Accessible.name: pauseText.text

            Text {
              id: pauseText
              anchors.centerIn: parent
              text: root.paused ? "Resume" : "Pause"
              textFormat: Text.PlainText
              color: root.paused ? root.foreground : root.text2
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }
            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              onEntered: { root.cursorMoved = true; root.cursor = "pause" }
              onClicked: root.togglePause()
            }
          }
        }

        // the problem line: one verb
        CursorSurface {
          visible: root.problem !== null
          width: parent.width
          hasCursor: root.cursor === "problem"
          foreground: root.foreground
          implicitHeight: Style.space(28)
          Accessible.role: Accessible.Button
          Accessible.name: root.problem ? root.problem.label + ", " + root.problem.verb : ""

          RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Style.space(8)
            anchors.rightMargin: Style.space(8)
            spacing: Style.space(8)
            Text {
              text: root.problem ? Model.glyphText(root.problem.glyph) : ""
              color: root.problem ? root.toneColor(root.problem.tone) : root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.icon
            }
            Text {
              Layout.fillWidth: true
              text: root.problem ? root.problem.label : ""
              textFormat: Text.PlainText
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              elide: Text.ElideRight
            }
            Text {
              text: root.problem ? "↵ " + root.problem.verb : ""
              textFormat: Text.PlainText
              color: root.text2
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }
          MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            onEntered: { root.cursorMoved = true; root.cursor = "problem" }
            onClicked: root.fixProblem()
          }
        }

        // the last dictation: preview, app · age · tag, and its keys while the cursor is on it
        CursorSurface {
          id: lastRow
          visible: root.last !== null
          width: parent.width
          hasCursor: root.cursor === "last"
          foreground: root.foreground
          implicitHeight: lastColumn.implicitHeight + Style.space(16)
          Accessible.role: Accessible.Button
          Accessible.name: root.last ? [root.last.headline, root.last.meta].filter(function(s) { return s !== "" }).join(", ") : ""

          Column {
            id: lastColumn
            x: Style.space(8)
            y: Style.space(8)
            width: parent.width - Style.space(16)
            spacing: Style.space(4)

            RowLayout {
              visible: root.last !== null && root.last.headline !== ""
              width: parent.width
              spacing: Style.space(8)
              Text {
                text: root.last ? Model.glyphText(root.last.glyph) : ""
                color: root.last ? root.toneColor(root.last.tone) : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.icon
              }
              Text {
                Layout.fillWidth: true
                text: root.last ? root.last.headline : ""
                textFormat: Text.PlainText
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                elide: Text.ElideRight
              }
            }
            Text {
              visible: text !== ""
              width: parent.width
              text: root.last ? root.last.preview : ""
              textFormat: Text.PlainText
              color: root.last && root.last.headline !== "" ? root.text2 : root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.Wrap
              maximumLineCount: 2
              elide: Text.ElideRight
            }
            RowLayout {
              width: parent.width
              spacing: Style.space(12)
              Text {
                Layout.fillWidth: true
                text: root.last ? root.last.meta : ""
                textFormat: Text.PlainText
                color: root.text2
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                elide: Text.ElideRight
              }
              Text {
                visible: lastRow.hasCursor && root.last !== null && root.last.enterVerb !== ""
                text: root.last ? "↵ " + root.last.enterVerb : ""
                textFormat: Text.PlainText
                color: root.text2
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                visible: lastRow.hasCursor && root.last !== null && root.last.canPaste
                text: "P paste"
                textFormat: Text.PlainText
                color: root.text2
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                visible: lastRow.hasCursor && root.last !== null && root.last.canRetry && root.last.enterVerb !== "retry"
                text: "R retry"
                textFormat: Text.PlainText
                color: root.text2
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
          }
          MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            onEntered: { root.cursorMoved = true; root.cursor = "last" }
            onClicked: root.activate("last")
          }
        }

        // Config · Doctor
        Row {
          spacing: Style.space(12)
          Repeater {
            model: [{ row: "config", label: "Config" }, { row: "doctor", label: "Doctor" }]
            CursorSurface {
              required property var modelData
              hasCursor: root.cursor === modelData.row
              foreground: root.foreground
              implicitWidth: footerText.implicitWidth + Style.space(16)
              implicitHeight: Style.space(28)
              Accessible.role: Accessible.Button
              Accessible.name: modelData.label

              Text {
                id: footerText
                anchors.centerIn: parent
                text: modelData.label
                textFormat: Text.PlainText
                color: root.text2
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }
              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                onEntered: { root.cursorMoved = true; root.cursor = modelData.row }
                onClicked: root.activate(modelData.row)
              }
            }
          }
        }
      }
    }
  }

  // hark's mark: three bars, short tall short
  component Mark: Item {
    id: mark
    property real size: 12
    property color color: "white"
    implicitWidth: size
    implicitHeight: size

    Row {
      anchors.centerIn: parent
      spacing: Math.max(1, Math.round(mark.size / 8))
      Repeater {
        model: [0.5, 1.0, 0.5]
        Rectangle {
          required property var modelData
          width: Math.max(2, Math.round(mark.size / 5))
          height: Math.round(mark.size * modelData)
          anchors.verticalCenter: parent.verticalCenter
          color: mark.color
        }
      }
    }
  }
}
