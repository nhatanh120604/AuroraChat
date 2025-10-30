import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Effects 6.6
import Qt.labs.platform 1.1 as Platform


ApplicationWindow {
    id: window
    visible: true
    width: 920
    height: 600
    title: "Aurora Chat"
    color: palette.window

    readonly property var palette: ({
        window: "#090C12",
        gradientTop: "#111927",
        gradientBottom: "#07090D",
        panel: "#121A26",
        surface: "#152033",
        card: "#1A2738",
        canvas: "#1F2D40",
        accent: "#E0C184",
        accentSoft: "#E0C18426",
        accentBold: "#F3DCA3",
        textPrimary: "#F4F7FB",
        textSecondary: "#A5AEC1",
        outline: "#222E42",
        success: "#35A57C",
        warning: "#CE6C6C"
    })

    readonly property var avatarDefaultColors: ({ top: "#6DE5AE", bottom: "#2C9C6D" })

    function avatarGradientFor(name) {
        return avatarDefaultColors
    }

    property var privateMessageModels: ({})
    property var privateSeenTransfers: ({}) // peer -> set/dict of transfer_ids
    property var privateDrafts: ({})
    property string publicDraft: ""
    property var conversationPages: ({})
    property int totalUserCount: 0
    property var publicTypingUsers: []
    property string publicTypingText: ""
    property var privateTypingStates: ({})
    property var messageIndexById: ({})
    property var emojiOptions: ["😀", "😂", "😍", "😎", "👍", "🙏", "🎉", "❤️", "🔥", "🤔", "🥳", "🤩", "😢", "😡"]
    property var publicPendingFile: null
    property var privatePendingFiles: ({})

    function formatTimestamp(ts) {
        try {
            if (ts && ts.length > 0) {
                var d = new Date(ts)
                if (!isNaN(d.getTime()))
                    return Qt.formatDateTime(d, "hh:mm ap")
            }
        } catch(e) {}
        return Qt.formatDateTime(new Date(), "hh:mm ap")
    }

    function ensurePrivateModel(peer) {
        if (!peer) {
            return null
        }
        if (!privateMessageModels[peer]) {
            privateMessageModels[peer] = Qt.createQmlObject('import QtQuick 2.15; ListModel {}', window)
        }
        return privateMessageModels[peer]
    }

    function conversationIndex(key) {
        for (var i = 0; i < conversationTabsModel.count; ++i) {
            if (conversationTabsModel.get(i).key === key) {
                return i
            }
        }
        return -1
    }

    function buildPublicTypingText(list) {
        if (!list || list.length === 0) {
            return ""
        }
        if (list.length === 1) {
            return list[0] + " is typing..."
        }
        if (list.length === 2) {
            return list[0] + " and " + list[1] + " are typing..."
        }
        return list[0] + ", " + list[1] + " and others are typing..."
    }

    function updatePublicTyping(username, isTyping) {
        if (!username || username.length === 0 || username === chatClient.username) {
            return
        }
        var current = publicTypingUsers.slice()
        var index = current.indexOf(username)
        if (isTyping && index === -1) {
            current.push(username)
        } else if (!isTyping && index !== -1) {
            current.splice(index, 1)
        }
        publicTypingUsers = current
        publicTypingText = buildPublicTypingText(current)
    }

    function updatePrivateTyping(peer, isTyping) {
        if (!peer || peer.length === 0) {
            return
        }
        var snapshot = Object.assign({}, privateTypingStates)
        if (isTyping) {
            snapshot[peer] = true
        } else if (snapshot[peer]) {
            delete snapshot[peer]
        }
        privateTypingStates = snapshot
    }

    function insertEmojiIntoField(field, emoji) {
        if (!field || !emoji || emoji.length === 0) {
            return
        }
        var cursor = field.cursorPosition
        var current = field.text || ""
        field.text = current.slice(0, cursor) + emoji + current.slice(cursor)
        field.cursorPosition = cursor + emoji.length
    }

    function formatFileSize(bytes) {
        var value = Number(bytes)
        if (!value || isNaN(value) || value <= 0) {
            return ""
        }
        if (value < 1024) {
            return value + " B"
        }
        if (value < 1024 * 1024) {
            return (value / 1024).toFixed(1) + " KB"
        }
        return (value / (1024 * 1024)).toFixed(1) + " MB"
    }

    function setPrivateMessageStatusById(messageId, status) {
        if (!messageId || messageId <= 0) {
            return
        }
        var target = messageIndexById[messageId]
        if (target) {
            var model = privateMessageModels[target.peer]
            if (model && target.index < model.count) {
                model.setProperty(target.index, "status", status)
                return
            }
        }
        var peers = Object.keys(privateMessageModels)
        for (var i = 0; i < peers.length; ++i) {
            var peer = peers[i]
            var model = privateMessageModels[peer]
            if (!model) {
                continue
            }
            for (var j = 0; j < model.count; ++j) {
                var row = model.get(j)
                if (row && row.messageId === messageId) {
                    model.setProperty(j, "status", status)
                    var updated = Object.assign({}, messageIndexById)
                    updated[messageId] = {"peer": peer, "index": j}
                    messageIndexById = updated
                    return
                }
            }
        }
    }

    function markPrivateMessagesAsRead(peer) {
        if (!peer || peer.length === 0) {
            return
        }
        var model = privateMessageModels[peer]
        if (!model) {
            return
        }
        var ids = []
        for (var i = 0; i < model.count; ++i) {
            var entry = model.get(i)
            if (!entry || entry.isOutgoing === true) {
                continue
            }
            if (!entry.messageId || entry.messageId <= 0) {
                continue
            }
            if (entry.readNotified === true) {
                continue
            }
            ids.push(entry.messageId)
            model.setProperty(i, "readNotified", true)
        }
        if (ids.length > 0) {
            chatClient.markPrivateMessagesRead(peer, ids)
        }
    }

    function setConversationUnread(key, unread) {
        var idx = conversationIndex(key)
        if (idx >= 0) {
            conversationTabsModel.setProperty(idx, "hasUnread", unread)
        }
    }

    function setPrivatePendingFile(peer, info) {
        if (!peer || peer.length === 0) {
            return
        }
        var snapshot = Object.assign({}, privatePendingFiles)
        if (info) {
            snapshot[peer] = info
        } else if (snapshot.hasOwnProperty(peer)) {
            delete snapshot[peer]
        }
        privatePendingFiles = snapshot
    }

    function getPrivatePendingFile(peer) {
        if (!peer || peer.length === 0) {
            return null
        }
        return privatePendingFiles[peer] ? privatePendingFiles[peer] : null
    }

    function registerConversationPage(key, page) {
        conversationPages[key] = page
    }

    function unregisterConversationPage(key) {
        delete conversationPages[key]
    }

    function focusActiveComposer() {
        var current = conversationTabsModel.count > 0 ? conversationTabsModel.get(conversationTabBar.currentIndex) : null
        if (!current) {
            return
        }
        var page = conversationPages[current.key]
        if (page && page.forceComposerFocus) {
            page.forceComposerFocus()
        }
    }

    function onConversationActivated(index) {
        if (index < 0 || index >= conversationTabsModel.count) {
            return
        }
        var tab = conversationTabsModel.get(index)
        var key = tab.key
        setConversationUnread(key, false)
        if (tab.isPrivate === true) {
            markPrivateMessagesAsRead(key)
        }
        focusActiveComposer()
        var page = conversationPages[key]
        if (page && page.scrollToEnd) {
            page.scrollToEnd(false)
        }
    }

    function appendPrivateMessage(peer, author, text, isOutgoing, messageId, status, filePayload, timestamp) {
        if (!peer || peer.length === 0) {
            return
        }
        text = text || ""
        if (text.length === 0) {
            if (!filePayload || !filePayload.name || !filePayload.data) {
                return
            }
        }
        // De-dup by transfer_id if present
        var tid = (filePayload && filePayload.transfer_id) ? String(filePayload.transfer_id) : ""
        if (tid && privateSeenTransfers[peer] && privateSeenTransfers[peer][tid]) {
            return
        }
        if (!author || author.length === 0) {
            author = isOutgoing === true ? (chatClient.username && chatClient.username.length > 0 ? chatClient.username : "You") : peer
        }
        var model = ensurePrivateModel(peer)
        if (!model) {
            return
        }
        if (tid) {
            if (!privateSeenTransfers[peer]) privateSeenTransfers[peer] = {}
            privateSeenTransfers[peer][tid] = true
        }
        if (conversationIndex(peer) === -1) {
            conversationTabsModel.append({"key": peer, "title": peer, "isPrivate": true, "hasUnread": false})
        }
        var resolvedStatus = status && status.length > 0 ? status : (isOutgoing === true ? "sent" : "delivered")
        var resolvedId = messageId && messageId > 0 ? messageId : 0
        var fileInfo = filePayload ? filePayload : null
        var fileName = fileInfo && fileInfo.name ? fileInfo.name : ""
        var fileMime = fileInfo && fileInfo.mime ? fileInfo.mime : ""
        var fileData = fileInfo && fileInfo.data ? fileInfo.data : ""
        var fileSize = fileInfo && fileInfo.size ? Number(fileInfo.size) : 0
        model.append({
            "user": author,
            "text": text,
            "isPrivate": true,
            "isOutgoing": isOutgoing === true,
            "displayContext": isOutgoing === true ? "You" : author,
            "status": resolvedStatus,
            "messageId": resolvedId,
            "readNotified": isOutgoing === true,
            "fileName": fileName,
            "fileMime": fileMime,
            "fileData": fileData,
            "fileSize": fileSize,
            "timestamp": timestamp ? timestamp : Qt.formatDateTime(new Date(), "hh:mm ap")
        })
        var newIndex = model.count - 1
        if (resolvedId > 0) {
            var mapping = Object.assign({}, messageIndexById)
            mapping[resolvedId] = {"peer": peer, "index": newIndex}
            messageIndexById = mapping
        }
        var idx = conversationIndex(peer)
        var isActive = idx === conversationTabBar.currentIndex
        if (!isActive && isOutgoing !== true) {
            setConversationUnread(peer, true)
        } else if (isActive && isOutgoing !== true) {
            markPrivateMessagesAsRead(peer)
        }
        var page = conversationPages[peer]
        if (page && page.scrollToEnd) {
            page.scrollToEnd(isActive)
        }
    }

    function openPrivateConversation(peer) {
        if (!peer || peer === chatClient.username) {
            return
        }
        ensurePrivateModel(peer)
        if (conversationIndex(peer) === -1) {
            conversationTabsModel.append({"key": peer, "title": peer, "isPrivate": true, "hasUnread": false})
        }
        var idx = conversationIndex(peer)
        if (idx >= 0) {
            conversationTabBar.currentIndex = idx
            focusActiveComposer()
        }
    }

    function closePrivateConversation(peer) {
        var idx = conversationIndex(peer)
        if (idx > 0) {
            var wasCurrent = conversationTabBar.currentIndex === idx
            setPrivatePendingFile(peer, null)
            conversationTabsModel.remove(idx)
            unregisterConversationPage(peer)
            var targetIndex = conversationTabBar.currentIndex
            if (wasCurrent) {
                targetIndex = Math.max(0, Math.min(idx - 1, conversationTabsModel.count - 1))
            }
            if (targetIndex >= conversationTabsModel.count) {
                targetIndex = Math.max(0, conversationTabsModel.count - 1)
            }
            conversationTabBar.currentIndex = targetIndex
        }
    }

    function resetPrivateConversations() {
        var keys = Object.keys(privateMessageModels)
        for (var i = 0; i < keys.length; ++i) {
            var key = keys[i]
            if (privateMessageModels[key]) {
                privateMessageModels[key].destroy()
            }
        }
        privateMessageModels = ({})
        privateDrafts = ({})
        publicDraft = ""
        conversationPages = ({})
        privateTypingStates = ({})
        messageIndexById = ({})
        publicTypingUsers = []
        publicTypingText = ""
        publicPendingFile = null
        privatePendingFiles = ({})
        while (conversationTabsModel.count > 1) {
            conversationTabsModel.remove(conversationTabsModel.count - 1)
        }
        setConversationUnread("public", false)
        conversationTabBar.currentIndex = 0
    }

    background: Rectangle {
        id: backgroundLayer
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: palette.gradientTop }
            GradientStop { position: 1.0; color: palette.gradientBottom }
        }

        property real shimmerOffset: -0.4

        Rectangle {
            anchors.fill: parent
            opacity: 0.12
            gradient: Gradient {
                GradientStop {
                    position: Math.max(0.0, Math.min(1.0, backgroundLayer.shimmerOffset - 0.25))
                    color: "#00000000"
                }
                GradientStop {
                    position: Math.max(0.0, Math.min(1.0, backgroundLayer.shimmerOffset))
                    color: "#22FFFFFF"
                }
                GradientStop {
                    position: Math.max(0.0, Math.min(1.0, backgroundLayer.shimmerOffset + 0.25))
                    color: "#00000000"
                }
            }
        }

        Item {
            anchors.fill: parent
            opacity: 0.35

            Rectangle {
                id: auroraGlowA
                width: 420
                height: 420
                radius: width / 2
                anchors.verticalCenter: parent.verticalCenter
                x: -260
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "#40E0C184" }
                    GradientStop { position: 1.0; color: "#00000000" }
                }
                SequentialAnimation on x {
                    loops: Animation.Infinite
                    NumberAnimation {
                        to: backgroundLayer.width - 160
                        duration: 28000
                        easing.type: Easing.InOutSine
                    }
                    NumberAnimation {
                        to: -260
                        duration: 28000
                        easing.type: Easing.InOutSine
                    }
                }
            }

            Rectangle {
                id: auroraGlowB
                width: 360
                height: 360
                radius: width / 2
                anchors.horizontalCenter: parent.horizontalCenter
                y: -180
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "#3035A57C" }
                    GradientStop { position: 1.0; color: "#00000000" }
                }
                SequentialAnimation on y {
                    loops: Animation.Infinite
                    NumberAnimation {
                        to: backgroundLayer.height - 140
                        duration: 24000
                        easing.type: Easing.InOutSine
                    }
                    NumberAnimation {
                        to: -180
                        duration: 24000
                        easing.type: Easing.InOutSine
                    }
                }
            }
        }

        NumberAnimation on shimmerOffset {
            loops: Animation.Infinite
            from: -0.4
            to: 1.4
            duration: 22000
            easing.type: Easing.InOutSine
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: 0
        color: "#14FFFFFF" // Subtle overlay
    }

    Item {
        id: contentRoot
        anchors.fill: parent
        opacity: 0
        y: 16

        ParallelAnimation {
            id: introAnimation
            running: false
            PropertyAnimation {
                target: contentRoot
                property: "opacity"
                to: 1
                duration: 320
                easing.type: Easing.OutCubic
            }
            PropertyAnimation {
                target: contentRoot
                property: "y"
                to: 0
                duration: 360
                easing.type: Easing.OutCubic
            }
        }

        Component.onCompleted: introAnimation.start()

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 32
            spacing: 26

        // --- HEADER CARD ---
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 132

            MultiEffect {
                anchors.fill: headerCard
                source: headerCard
                shadowEnabled: true
                shadowHorizontalOffset: 0
                shadowVerticalOffset: 18
                shadowBlur: 32
                shadowColor: "#28000000"
                autoPaddingEnabled: true
            }

            Rectangle {
                id: headerCard
                anchors.fill: parent
                radius: 28
                transformOrigin: Item.Center
                border.color: palette.outline
                border.width: 1
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "#1C283B" } // Using custom gradient
                    GradientStop { position: 1.0; color: palette.panel }
                }

                Item {
                    anchors.fill: parent
                    anchors.margins: 28

                    GridLayout {
                        anchors.fill: parent
                        columns: 2
                        columnSpacing: 28
                        rowSpacing: 0

                        // --- Header Welcome Text ---
                        ColumnLayout {
                            Layout.row: 0
                            Layout.column: 0
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignVCenter
                            spacing: 10

                            Text {
                                text: chatClient.username.length > 0 ? "Welcome back, " + chatClient.username : "Aurora Lounge"
                                color: palette.textPrimary
                                font.pixelSize: 26
                                font.bold: true
                                Layout.alignment: Qt.AlignVCenter
                            }

                            Text {
                                text: chatClient.username.length > 0 ? "Share a thought with the lounge—your voice sets tonight's tone." : "Reserve a signature name to slip past the velvet rope."
                                color: palette.textSecondary
                                font.pixelSize: 14
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }

                            RowLayout {
                                spacing: 12

                                Rectangle {
                                    Layout.preferredWidth: 160
                                    Layout.preferredHeight: 32
                                    radius: 16
                                    border.width: 0
                                    color: chatClient.username.length > 0 ? palette.success : palette.accentSoft

                                    Text {
                                        anchors.centerIn: parent
                                        text: chatClient.username.length > 0 ? "Enrolled guest" : "Guest access"
                                        color: chatClient.username.length > 0 ? palette.textPrimary : palette.textSecondary
                                        font.pixelSize: 12
                                        font.bold: true
                                    }
                                }

                                Rectangle {
                                    Layout.preferredWidth: 160
                                    Layout.preferredHeight: 32
                                    radius: 16
                                    color: "transparent"
                                    border.color: palette.outline
                                    border.width: 1

                                    Timer {
                                        id: clockTimer
                                        interval: 1000 // Updates once per second
                                        running: true
                                        repeat: true
                                    }

                                    Text {
                                        anchors.centerIn: parent
                                        text: {
                                            clockTimer.triggered() // Depend on the timer
                                            return Qt.formatDateTime(new Date(), "ddd, MMM d • hh:mm ap")
                                        }
                                        color: palette.textSecondary
                                        font.pixelSize: 12
                                    }
                                }
                            }
                        }

                        // --- Header User Input ---
                        ColumnLayout {
                            Layout.row: 0
                            Layout.column: 1
                            Layout.preferredWidth: 320
                            Layout.alignment: Qt.AlignVCenter
                            spacing: 12

                            Rectangle {
                                // *** BUG FIX: Added Layout.fillWidth ***
                                Layout.fillWidth: true
                                Layout.preferredHeight: 44
                                radius: 18
                                color: window.palette.canvas
                                border.color: usernameField.activeFocus ? window.palette.accent : window.palette.outline
                                border.width: 1

                                TextField {
                                    id: usernameField
                                    anchors.fill: parent
                                    leftPadding: 18
                                    rightPadding: 18
                                    topPadding: 10
                                    bottomPadding: 10
                                    placeholderText: "Choose your lounge signature"
                                    placeholderTextColor: window.palette.textSecondary
                                    color: window.palette.textPrimary
                                    selectionColor: window.palette.accent
                                    selectedTextColor: window.palette.textPrimary
                                    verticalAlignment: Text.AlignVCenter
                                    font.pixelSize: 14
                                    enabled: chatClient.username.length === 0
                                    cursorDelegate: Rectangle {
                                        width: 2
                                        color: window.palette.accent
                                    }
                                    background: null
                                    selectByMouse: true
                                    onAccepted: registerButton.clicked()
                                }
                            }

                            RowLayout {
                                spacing: 12

                                Button {
                                    id: registerButton
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 44
                                    enabled: chatClient.username.length === 0
                                    padding: 0
                                    topPadding: 0
                                    bottomPadding: 0
                                    leftPadding: 0
                                    rightPadding: 0
                                    transformOrigin: Item.Center
                                    scale: down ? 0.95 : (hovered ? 1.04 : 1.0)
                                    Behavior on scale {
                                        NumberAnimation {
                                            duration: 160
                                            easing.type: Easing.OutQuad
                                        }
                                    }
                                    background: Rectangle {
                                        id: registerBackground
                                        radius: 20
                                        border.width: 0
                                        readonly property color enabledTop: palette.accentBold
                                        readonly property color enabledBottom: palette.accent
                                        readonly property color disabledTop: "#3A465D"
                                        readonly property color disabledBottom: "#2A354A"
                                        color: registerButton.enabled ? enabledBottom : disabledBottom
                                        readonly property color topColor: registerButton.enabled ? enabledTop : disabledTop
                                        readonly property color bottomColor: registerButton.enabled ? enabledBottom : disabledBottom
                                        gradient: Gradient {
                                            GradientStop { position: 0.0; color: registerBackground.topColor }
                                            GradientStop { position: 1.0; color: registerBackground.bottomColor }
                                        }

                                        Rectangle {
                                            anchors.fill: parent
                                            radius: parent.radius
                                            gradient: Gradient {
                                                GradientStop { position: 0.0; color: "#40FFFFFF" }
                                                GradientStop { position: 1.0; color: "#00FFFFFF" }
                                            }
                                            opacity: registerButton.down ? 0.5 : ((registerButton.hovered && registerButton.enabled) ? 0.25 : 0.0)
                                            Behavior on opacity {
                                                NumberAnimation {
                                                    duration: 160
                                                    easing.type: Easing.OutQuad
                                                }
                                            }
                                        }
                                    }
                                    contentItem: Text {
                                        anchors.centerIn: parent
                                        text: chatClient.username.length > 0 ? "Registered" : "Enter the lounge"
                                        color: palette.panel
                                        font.pixelSize: 14
                                        font.bold: true
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                    }
                                    onClicked: {
                                        var name = usernameField.text.trim()
                                        if (name.length > 0) {
                                            chatClient.register(name)
                                        }
                                    }
                                }

                                ToolButton {
                                    Layout.preferredWidth: 44
                                    Layout.preferredHeight: 44
                                    onClicked: chatClient.disconnect()
                                    padding: 0
                                    topPadding: 0
                                    bottomPadding: 0
                                    leftPadding: 0
                                    rightPadding: 0
                                    background: Rectangle {
                                        radius: 20
                                        color: palette.surface
                                        border.color: palette.outline
                                    }
                                    contentItem: Text {
                                        anchors.fill: parent
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                        text: "\u2715"
                                        color: palette.textSecondary
                                        font.pixelSize: 20
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // --- MAIN CONTENT (CHAT + USER LIST) ---
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 22

            // --- CHAT PANEL ---
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                MultiEffect {
                    anchors.fill: chatCard
                    source: chatCard
                    shadowEnabled: true
                    shadowHorizontalOffset: 0
                    shadowVerticalOffset: 18
                    shadowBlur: 36
                    shadowColor: "#22000000"
                    autoPaddingEnabled: true
                }

                Rectangle {
                    id: chatCard
                    anchors.fill: parent
                    radius: 26
                    transformOrigin: Item.Center
                    border.color: palette.outline
                    border.width: 1
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: palette.panel }
                        GradientStop { position: 1.0; color: palette.surface }
                    }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 28
                        spacing: 16

                        TabBar {
                            id: conversationTabBar
                            Layout.fillWidth: true
                            contentHeight: 38
                            background: Rectangle {
                                color: "transparent"
                                border.width: 0
                            }
                            Repeater {
                                model: conversationTabsModel
                                delegate: TabButton {
                                    required property string key
                                    required property string title
                                    required property bool isPrivate
                                    required property bool hasUnread
                                    implicitHeight: conversationTabBar.contentHeight
                                    padding: 0
                                    text: title
                                    checkable: true
                                    checked: TabBar.index === conversationTabBar.currentIndex
                                    font.pixelSize: 12
                                    background: Rectangle {
                                        radius: 16
                                        color: checked ? window.palette.canvas : window.palette.surface
                                        border.color: checked ? window.palette.accent : window.palette.outline
                                        border.width: checked ? 1 : 0
                                    }
                                    contentItem: Item {
                                        anchors.fill: parent
                                        RowLayout {
                                            anchors.fill: parent
                                            anchors.margins: 10
                                            spacing: 8

                                            Text {
                                                text: title
                                                color: window.palette.textPrimary
                                                font.pixelSize: 12
                                                font.bold: checked
                                                Layout.alignment: Qt.AlignVCenter
                                            }

                                            Rectangle {
                                                Layout.preferredWidth: 6
                                                Layout.preferredHeight: 6
                                                radius: 3
                                                color: window.palette.accent
                                                Layout.alignment: Qt.AlignVCenter
                                                visible: hasUnread
                                            }

                                            Item {
                                                Layout.fillWidth: true
                                                Layout.alignment: Qt.AlignVCenter
                                            }

                                            Item {
                                                Layout.preferredWidth: isPrivate ? 20 : 0
                                                Layout.fillHeight: true
                                                visible: isPrivate

                                                MouseArea {
                                                    anchors.fill: parent
                                                    hoverEnabled: true
                                                    onClicked: window.closePrivateConversation(key)

                                                    Text {
                                                        anchors.centerIn: parent
                                                        text: "\u2715"
                                                        color: window.palette.textSecondary
                                                        font.pixelSize: 12
                                                    }
                                                }
                                            }
                                        }
                                    }

                                    onClicked: conversationTabBar.currentIndex = TabBar.index
                                }
                            }
                        }

                        StackLayout {
                            id: conversationStack
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            currentIndex: conversationTabBar.currentIndex
                            onCurrentIndexChanged: window.onConversationActivated(currentIndex)

                            Repeater {
                                model: conversationTabsModel
                                delegate: Loader {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    sourceComponent: isPrivate ? privateConversationComponent : publicConversationComponent
                                    onLoaded: {
                                        if (sourceComponent === privateConversationComponent) {
                                            item.setup(key, title, window.ensurePrivateModel(key))
                                        } else if (sourceComponent === publicConversationComponent) {
                                            item.setup()
                                        }
                                        window.registerConversationPage(key, item)
                                    }
                                    onItemChanged: {
                                        if (!item) {
                                            window.unregisterConversationPage(key)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // --- CONCIERGE (USER LIST) PANEL ---
            Item {
                Layout.preferredWidth: 260
                Layout.fillHeight: true

                MultiEffect {
                    anchors.fill: conciergeCard
                    source: conciergeCard
                    shadowEnabled: true
                    shadowHorizontalOffset: 0
                    shadowVerticalOffset: 18
                    shadowBlur: 32
                    shadowColor: "#22000000"
                    autoPaddingEnabled: true
                }

                Rectangle {
                    id: conciergeCard
                    anchors.fill: parent
                    radius: 26
                    transformOrigin: Item.Center
                    border.color: palette.outline
                    border.width: 1
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: palette.surface }
                        GradientStop { position: 1.0; color: palette.panel }
                    }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 24
                        spacing: 18

                        Text {
                            text: "Concierge"
                            color: palette.textPrimary
                            font.pixelSize: 17
                            font.bold: true
                        }

                        Text {
                            text: "Spot a guest, tap to begin a private exchange."
                            color: palette.textSecondary
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 1
                            color: palette.outline
                            opacity: 0.18
                        }

                        ListView {
                            id: usersView
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            spacing: 12
                            model: usersModel
                            delegate: userDelegateComponent
                        }
                    }
                }
            }
        }
    }

    // --- MODELS ---
    ListModel {
        id: conversationTabsModel
        ListElement { key: "public"; title: "Salon feed"; isPrivate: false; hasUnread: false }
    }
    ListModel { id: messagesModel }
    ListModel { id: usersModel }

    // --- FEEDBACK ANIMATIONS ---
    ParallelAnimation {
        id: disconnectAnimation
        running: false

        SequentialAnimation {
            PropertyAnimation {
                target: headerCard
                property: "scale"
                to: 0.96
                duration: 130
                easing.type: Easing.InOutQuad
            }
            PropertyAnimation {
                target: headerCard
                property: "scale"
                to: 1
                duration: 220
                easing.type: Easing.OutBack
            }
        }

        SequentialAnimation {
            PropertyAnimation {
                target: chatCard
                property: "scale"
                to: 0.95
                duration: 130
                easing.type: Easing.InOutQuad
            }
            PropertyAnimation {
                target: chatCard
                property: "scale"
                to: 1
                duration: 240
                easing.type: Easing.OutBack
            }
        }

        SequentialAnimation {
            PropertyAnimation {
                target: conciergeCard
                property: "scale"
                to: 0.95
                duration: 130
                easing.type: Easing.InOutQuad
            }
            PropertyAnimation {
                target: conciergeCard
                property: "scale"
                to: 1
                duration: 220
                easing.type: Easing.OutBack
            }
        }
    }

    // --- COMPONENT DEFINITIONS ---
    Component {
        id: publicConversationComponent

        ColumnLayout {
            id: publicConversation
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 20

            function setup() {
                messageField.text = window.publicDraft || ""
                publicTypingTimer.stop()
                scrollArea.scrollToEnd(false)
            }

            function scrollToEnd(animated) {
                scrollArea.scrollToEnd(animated)
            }

            function forceComposerFocus() {
                messageField.forceActiveFocus()
            }

            Timer {
                id: publicTypingTimer
                interval: 2000
                repeat: false
                onTriggered: chatClient.indicatePublicTyping(false)
            }

            Menu {
                id: publicEmojiMenu
                Repeater {
                    model: window.emojiOptions
                    delegate: MenuItem {
                        text: modelData
                        onTriggered: {
                            window.insertEmojiIntoField(messageField, modelData)
                            messageField.forceActiveFocus()
                        }
                    }
                }
            }

            Platform.FileDialog {
                id: publicFileDialog
                title: "Select a file to share"
                onAccepted: {
                    var target = ""
                    if (publicFileDialog.file && publicFileDialog.file.toString) {
                        target = publicFileDialog.file.toString()
                    } else if (publicFileDialog.file) {
                        target = String(publicFileDialog.file)
                    } else if (publicFileDialog.files && publicFileDialog.files.length > 0) {
                        var candidate = publicFileDialog.files[0]
                        target = candidate && candidate.toString ? candidate.toString() : String(candidate)
                    }
                    if (target.length > 0) {
                        var meta = chatClient.inspectFile(target)
                        if (meta && meta.path) {
                            window.publicPendingFile = meta
                            messageField.forceActiveFocus()
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                Text {
                    text: "Salon feed"
                    color: palette.textPrimary
                    font.pixelSize: 18
                    font.bold: true
                }

                Rectangle {
                    Layout.preferredWidth: 6
                    Layout.preferredHeight: 6
                    radius: 3
                    color: palette.accent
                    Layout.alignment: Qt.AlignVCenter
                }

                Text {
                    text: window.totalUserCount > 0 ? window.totalUserCount + " guests mingling" : "Awaiting first arrival"
                    color: palette.textSecondary
                    font.pixelSize: 12
                    Layout.alignment: Qt.AlignVCenter
                }
            }

            Flickable {
                id: scrollArea
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                property bool autoStickToBottom: true
                contentWidth: chatColumn.width
                contentHeight: chatColumn.height
                boundsBehavior: Flickable.DragAndOvershootBounds

                Component.onCompleted: scrollToEnd(false)

                Column {
                    id: chatColumn
                    width: scrollArea.width
                    spacing: 20

                    Repeater {
                        model: messagesModel
                        delegate: messageDelegateComponent
                    }

                    Item {
                        width: parent.width
                        height: 12
                    }
                }

                ScrollIndicator.vertical: ScrollIndicator {
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    contentItem: Rectangle {
                        radius: 2
                        color: palette.accent
                    }
                }

                onContentHeightChanged: scrollToEnd(autoStickToBottom)
                onContentYChanged: {
                    if ((moving || dragging) && !atYEnd) {
                        autoStickToBottom = false
                    } else if (!moving && !dragging && atYEnd) {
                        autoStickToBottom = true
                    }
                }
                onMovementStarted: {
                    if (!atYEnd) {
                        autoStickToBottom = false
                    }
                }
                onMovementEnded: {
                    if (atYEnd) {
                        autoStickToBottom = true
                    }
                }
                onDraggingChanged: {
                    if (dragging && !atYEnd) {
                        autoStickToBottom = false
                    } else if (!dragging && atYEnd) {
                        autoStickToBottom = true
                    }
                }

                NumberAnimation {
                    id: scrollAnimation
                    target: scrollArea
                    property: "contentY"
                    duration: 220
                    easing.type: Easing.InOutQuad
                }

                function scrollToEnd(animated) {
                    var target = Math.max(0, contentHeight - height)
                    if (Math.abs(contentY - target) < 0.5) {
                        return
                    }
                    if (animated) {
                        if (!autoStickToBottom) {
                            return
                        }
                        scrollAnimation.stop()
                        scrollAnimation.from = contentY
                        scrollAnimation.to = target
                        scrollAnimation.restart()
                    } else {
                        scrollAnimation.stop()
                        contentY = target
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: window.publicTypingText
                color: palette.textSecondary
                font.pixelSize: 12
                visible: text && text.length > 0
            }

            Rectangle {
                Layout.fillWidth: true
                radius: 22
                color: window.palette.surface
                border.color: window.palette.outline
                border.width: 1
                implicitHeight: composerContent.implicitHeight + 36

                ColumnLayout {
                    id: composerContent
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 12

                    RowLayout {
                        id: publicComposerRow
                        Layout.fillWidth: true
                        spacing: 16

                        Rectangle {
                            id: messageFieldFrame
                            Layout.fillWidth: true
                            Layout.preferredHeight: 48
                            radius: 16
                            color: window.palette.canvas
                            border.color: messageField.activeFocus ? window.palette.accent : window.palette.outline
                            border.width: 1
                            property real rippleProgress: 0
                            Behavior on border.color {
                                ColorAnimation {
                                    duration: 180
                                    easing.type: Easing.OutQuad
                                }
                            }

                            Rectangle {
                                id: rippleOverlay
                                anchors.fill: parent
                                radius: parent.radius
                                color: "transparent"
                                border.width: 0
                                opacity: messageField.activeFocus ? 0.35 : 0.0
                                visible: opacity > 0
                                gradient: Gradient {
                                    GradientStop {
                                        position: Math.max(0.0, messageFieldFrame.rippleProgress - 0.2)
                                        color: "#00FFFFFF"
                                    }
                                    GradientStop {
                                        position: Math.max(0.0, Math.min(1.0, messageFieldFrame.rippleProgress))
                                        color: window.palette.accent
                                    }
                                    GradientStop {
                                        position: Math.min(1.0, messageFieldFrame.rippleProgress + 0.2)
                                        color: "#00FFFFFF"
                                    }
                                }
                                Behavior on opacity {
                                    NumberAnimation {
                                        duration: 220
                                        easing.type: Easing.OutQuad
                                    }
                                }
                            }

                            NumberAnimation {
                                id: rippleAnimator
                                target: messageFieldFrame
                                property: "rippleProgress"
                                from: 0
                                to: 1
                                duration: 520
                                easing.type: Easing.OutQuad
                            }

                            TextField {
                                id: messageField
                                anchors.fill: parent
                                leftPadding: 18
                                rightPadding: 18
                                topPadding: 12
                                bottomPadding: 12
                                placeholderText: "Share something with the lounge"
                                placeholderTextColor: window.palette.textSecondary
                                color: window.palette.textPrimary
                                selectionColor: window.palette.accent
                                selectedTextColor: window.palette.textPrimary
                                verticalAlignment: Text.AlignVCenter
                                font.pixelSize: 14
                                wrapMode: Text.WordWrap
                                cursorDelegate: Rectangle {
                                    width: 2
                                    color: window.palette.accent
                                }
                                background: null
                                selectByMouse: true
                                onAccepted: sendButton.clicked()
                                onActiveFocusChanged: {
                                    if (activeFocus) {
                                        messageFieldFrame.rippleProgress = 0
                                        rippleAnimator.restart()
                                    } else {
                                        rippleAnimator.stop()
                                        messageFieldFrame.rippleProgress = 0
                                        chatClient.indicatePublicTyping(false)
                                        publicTypingTimer.stop()
                                    }
                                }
                                onTextChanged: {
                                    window.publicDraft = text
                                    if (text.length > 0) {
                                        chatClient.indicatePublicTyping(true)
                                        publicTypingTimer.restart()
                                    } else {
                                        chatClient.indicatePublicTyping(false)
                                        publicTypingTimer.stop()
                                    }
                                }
                            }
                        }

                        ToolButton {
                            id: publicEmojiButton
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            padding: 0
                            focusPolicy: Qt.NoFocus
                            property color baseColor: window.palette.canvas
                            property color hoverColor: window.palette.surface
                            property color activeColor: window.palette.accentSoft
                            property color borderColor: window.palette.outline
                            text: "😊"
                            font.pixelSize: 20
                            onClicked: {
                                var pos = mapToItem(null, 0, height)
                                publicEmojiMenu.x = pos.x
                                publicEmojiMenu.y = pos.y
                                publicEmojiMenu.open()
                            }
                            background: Rectangle {
                                radius: 18
                                color: publicEmojiButton.down ? publicEmojiButton.activeColor : (publicEmojiButton.hovered ? publicEmojiButton.hoverColor : publicEmojiButton.baseColor)
                                border.width: 1
                                border.color: publicEmojiButton.down || publicEmojiButton.hovered ? window.palette.accent : publicEmojiButton.borderColor
                                Behavior on color {
                                    ColorAnimation { duration: 140; easing.type: Easing.OutQuad }
                                }
                                Behavior on border.color {
                                    ColorAnimation { duration: 140; easing.type: Easing.OutQuad }
                                }
                            }
                            contentItem: Text {
                                anchors.fill: parent
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                text: publicEmojiButton.text
                                color: window.palette.textPrimary
                                font.pixelSize: publicEmojiButton.font.pixelSize
                            }
                        }

                        ToolButton {
                            id: publicFileButton
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            padding: 0
                            focusPolicy: Qt.NoFocus
                            property color baseColor: window.palette.canvas
                            property color hoverColor: window.palette.surface
                            property color activeColor: window.palette.accentSoft
                            property color borderColor: window.palette.outline
                            text: "📎"
                            font.pixelSize: 18
                            onClicked: publicFileDialog.open()
                            background: Rectangle {
                                radius: 18
                                color: publicFileButton.down ? publicFileButton.activeColor : (publicFileButton.hovered ? publicFileButton.hoverColor : publicFileButton.baseColor)
                                border.width: 1
                                border.color: publicFileButton.down || publicFileButton.hovered ? window.palette.accent : publicFileButton.borderColor
                                Behavior on color {
                                    ColorAnimation { duration: 140; easing.type: Easing.OutQuad }
                                }
                                Behavior on border.color {
                                    ColorAnimation { duration: 140; easing.type: Easing.OutQuad }
                                }
                            }
                            contentItem: Text {
                                anchors.fill: parent
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                text: publicFileButton.text
                                color: window.palette.textPrimary
                                font.pixelSize: publicFileButton.font.pixelSize
                            }
                        }

                        Button {
                            id: sendButton
                            Layout.preferredWidth: 124
                            Layout.preferredHeight: 48
                            padding: 0
                            topPadding: 0
                            bottomPadding: 0
                            leftPadding: 0
                            rightPadding: 0
                            transformOrigin: Item.Center
                            scale: down ? 0.95 : (hovered ? 1.05 : 1.0)
                            Behavior on scale {
                                NumberAnimation {
                                    duration: 160
                                    easing.type: Easing.OutQuad
                                }
                            }
                            background: Rectangle {
                                id: sendBackground
                                radius: 22
                                border.width: 0
                                gradient: Gradient {
                                    GradientStop { position: 0.0; color: palette.accentBold }
                                    GradientStop { position: 1.0; color: palette.accent }
                                }
                                Rectangle {
                                    anchors.fill: parent
                                    radius: parent.radius
                                    gradient: Gradient {
                                        GradientStop { position: 0.0; color: "#40FFFFFF" }
                                        GradientStop { position: 1.0; color: "#00FFFFFF" }
                                    }
                                    opacity: sendButton.down ? 0.55 : (sendButton.hovered ? 0.28 : 0.0)
                                    Behavior on opacity {
                                        NumberAnimation {
                                            duration: 160
                                            easing.type: Easing.OutQuad
                                        }
                                    }
                                }
                            }
                            contentItem: Text {
                                anchors.centerIn: parent
                                text: "Send"
                                color: palette.panel
                                font.pixelSize: 15
                                font.bold: true
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            onClicked: {
                                var text = messageField.text.trim()
                                var pendingPath = window.publicPendingFile && window.publicPendingFile.path ? window.publicPendingFile.path : ""
                                if (text.length === 0 && pendingPath.length === 0) {
                                    chatClient.sendMessageWithAttachment("", "")
                                    return
                                }
                                chatClient.sendMessageWithAttachment(text, pendingPath)
                                if (text.length > 0 || pendingPath.length > 0) {
                                    messageField.text = ""
                                    window.publicDraft = ""
                                    window.publicPendingFile = null
                                    chatClient.indicatePublicTyping(false)
                                    publicTypingTimer.stop()
                                    scrollArea.scrollToEnd(true)
                                }
                            }
                        }
                    }

                    Rectangle {
                        id: publicAttachmentPreview
                        Layout.fillWidth: true
                        visible: window.publicPendingFile !== null
                        radius: 14
                        color: window.palette.canvas
                        border.color: window.palette.outline
                        border.width: 1
                        implicitHeight: publicAttachmentRow.implicitHeight + 16

                        RowLayout {
                            id: publicAttachmentRow
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 12

                            Text {
                                text: "📎"
                                font.pixelSize: 20
                                color: window.palette.accent
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2

                                Text {
                                    text: window.publicPendingFile ? window.publicPendingFile.name : ""
                                    color: window.palette.textPrimary
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Text {
                                    text: window.publicPendingFile ? window.formatFileSize(window.publicPendingFile.size) : ""
                                    color: window.palette.textSecondary
                                    font.pixelSize: 11
                                    Layout.fillWidth: true
                                }
                            }

                            ToolButton {
                                Layout.preferredWidth: 28
                                Layout.preferredHeight: 28
                                padding: 0
                                text: "\u2715"
                                onClicked: {
                                    window.publicPendingFile = null
                                    messageField.forceActiveFocus()
                                }
                                background: Rectangle {
                                    radius: 12
                                    color: window.palette.surface
                                    border.color: window.palette.outline
                                    border.width: 1
                                }
                                contentItem: Text {
                                    anchors.centerIn: parent
                                    text: parent.text
                                    color: window.palette.textSecondary
                                    font.pixelSize: 12
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Component {
        id: privateConversationComponent

        ColumnLayout {
            id: privateConversation
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 20
            property string peerKey: ""
            property string displayName: ""
            property var conversationModel: null
            property var pendingFile: null

            function setup(key, title, model) {
                peerKey = key || ""
                displayName = title || key || ""
                conversationModel = model
                messageField.text = window.privateDrafts[peerKey] || ""
                pendingFile = window.getPrivatePendingFile(peerKey)
                privateTypingTimer.stop()
                scrollArea.scrollToEnd(false)
            }

            function scrollToEnd(animated) {
                scrollArea.scrollToEnd(animated)
            }

            function forceComposerFocus() {
                messageField.forceActiveFocus()
            }

            Timer {
                id: privateTypingTimer
                interval: 2000
                repeat: false
                onTriggered: {
                    if (peerKey.length > 0) {
                        chatClient.indicatePrivateTyping(peerKey, false)
                    }
                }
            }

            Menu {
                id: privateEmojiMenu
                Repeater {
                    model: window.emojiOptions
                    delegate: MenuItem {
                        text: modelData
                        onTriggered: {
                            window.insertEmojiIntoField(messageField, modelData)
                            messageField.forceActiveFocus()
                        }
                    }
                }
            }

            Platform.FileDialog {
                id: privateFileDialog
                title: "Select a file to whisper"
                onAccepted: {
                    if (peerKey.length === 0) {
                        return
                    }
                    var target = ""
                    if (privateFileDialog.file && privateFileDialog.file.toString) {
                        target = privateFileDialog.file.toString()
                    } else if (privateFileDialog.file) {
                        target = String(privateFileDialog.file)
                    } else if (privateFileDialog.files && privateFileDialog.files.length > 0) {
                        var candidate = privateFileDialog.files[0]
                        target = candidate && candidate.toString ? candidate.toString() : String(candidate)
                    }
                    if (target.length > 0) {
                        var meta = chatClient.inspectFile(target)
                        if (meta && meta.path) {
                            pendingFile = meta
                            window.setPrivatePendingFile(peerKey, meta)
                            messageField.forceActiveFocus()
                            chatClient.indicatePrivateTyping(peerKey, false)
                            privateTypingTimer.stop()
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                Text {
                    text: displayName.length > 0 ? "Private line with " + displayName : "Private line"
                    color: palette.textPrimary
                    font.pixelSize: 18
                    font.bold: true
                }

                Rectangle {
                    Layout.preferredWidth: 6
                    Layout.preferredHeight: 6
                    radius: 3
                    color: palette.accent
                    Layout.alignment: Qt.AlignVCenter
                }

                Text {
                    text: "Only you and " + (displayName.length > 0 ? displayName : "this guest") + " can see this thread"
                    color: palette.textSecondary
                    font.pixelSize: 12
                    Layout.alignment: Qt.AlignVCenter
                    wrapMode: Text.WordWrap
                }
            }

            Text {
                Layout.fillWidth: true
                text: peerKey.length > 0 && window.privateTypingStates[peerKey] ? peerKey + " is typing..." : ""
                color: palette.textSecondary
                font.pixelSize: 12
                visible: text.length > 0
            }

            Flickable {
                id: scrollArea
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                property bool autoStickToBottom: true
                contentWidth: chatColumn.width
                contentHeight: chatColumn.height
                boundsBehavior: Flickable.DragAndOvershootBounds

                Component.onCompleted: scrollToEnd(false)

                Column {
                    id: chatColumn
                    width: scrollArea.width
                    spacing: 20

                    Repeater {
                        model: privateConversation.conversationModel ? privateConversation.conversationModel : 0
                        delegate: messageDelegateComponent
                    }

                    Item {
                        width: parent.width
                        height: 12
                    }
                }

                ScrollIndicator.vertical: ScrollIndicator {
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    contentItem: Rectangle {
                        radius: 2
                        color: palette.accent
                    }
                }

                onContentHeightChanged: scrollToEnd(autoStickToBottom)
                onContentYChanged: {
                    if ((moving || dragging) && !atYEnd) {
                        autoStickToBottom = false
                    } else if (!moving && !dragging && atYEnd) {
                        autoStickToBottom = true
                    }
                }
                onMovementStarted: {
                    if (!atYEnd) {
                        autoStickToBottom = false
                    }
                }
                onMovementEnded: {
                    if (atYEnd) {
                        autoStickToBottom = true
                    }
                }
                onDraggingChanged: {
                    if (dragging && !atYEnd) {
                        autoStickToBottom = false
                    } else if (!dragging && atYEnd) {
                        autoStickToBottom = true
                    }
                }

                NumberAnimation {
                    id: privateScrollAnimation
                    target: scrollArea
                    property: "contentY"
                    duration: 220
                    easing.type: Easing.InOutQuad
                }

                function scrollToEnd(animated) {
                    var target = Math.max(0, contentHeight - height)
                    if (Math.abs(contentY - target) < 0.5) {
                        return
                    }
                    if (animated) {
                        if (!autoStickToBottom) {
                            return
                        }
                        privateScrollAnimation.stop()
                        privateScrollAnimation.from = contentY
                        privateScrollAnimation.to = target
                        privateScrollAnimation.restart()
                    } else {
                        privateScrollAnimation.stop()
                        contentY = target
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                radius: 22
                color: window.palette.surface
                border.color: window.palette.outline
                border.width: 1
                implicitHeight: privateComposerContent.implicitHeight + 36

                ColumnLayout {
                    id: privateComposerContent
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 16

                        Rectangle {
                            id: privateMessageFieldFrame
                            Layout.fillWidth: true
                            Layout.preferredHeight: 48
                            radius: 16
                            color: window.palette.canvas
                            border.color: messageField.activeFocus ? window.palette.accent : window.palette.outline
                            border.width: 1
                            property real rippleProgress: 0
                            Behavior on border.color {
                                ColorAnimation {
                                    duration: 180
                                    easing.type: Easing.OutQuad
                                }
                            }

                            Rectangle {
                                anchors.fill: parent
                                radius: parent.radius
                                color: "transparent"
                                border.width: 0
                                opacity: messageField.activeFocus ? 0.35 : 0.0
                                visible: opacity > 0
                                gradient: Gradient {
                                    GradientStop {
                                        position: Math.max(0.0, privateMessageFieldFrame.rippleProgress - 0.2)
                                        color: "#00FFFFFF"
                                    }
                                    GradientStop {
                                        position: Math.max(0.0, Math.min(1.0, privateMessageFieldFrame.rippleProgress))
                                        color: window.palette.accent
                                    }
                                    GradientStop {
                                        position: Math.min(1.0, privateMessageFieldFrame.rippleProgress + 0.2)
                                        color: "#00FFFFFF"
                                    }
                                }
                                Behavior on opacity {
                                    NumberAnimation {
                                        duration: 220
                                        easing.type: Easing.OutQuad
                                    }
                                }
                            }

                            NumberAnimation {
                                id: privateRippleAnimator
                                target: privateMessageFieldFrame
                                property: "rippleProgress"
                                from: 0
                                to: 1
                                duration: 520
                                easing.type: Easing.OutQuad
                            }

                            TextField {
                                id: messageField
                                anchors.fill: parent
                                leftPadding: 18
                                rightPadding: 18
                                topPadding: 12
                                bottomPadding: 12
                                placeholderText: displayName.length > 0 ? "Whisper to " + displayName : "Whisper to this guest"
                                placeholderTextColor: window.palette.textSecondary
                                color: window.palette.textPrimary
                                selectionColor: window.palette.accent
                                selectedTextColor: window.palette.textPrimary
                                verticalAlignment: Text.AlignVCenter
                                font.pixelSize: 14
                                wrapMode: Text.WordWrap
                                cursorDelegate: Rectangle {
                                    width: 2
                                    color: window.palette.accent
                                }
                                background: null
                                selectByMouse: true
                                onAccepted: sendButton.clicked()
                                onActiveFocusChanged: {
                                    if (activeFocus) {
                                        privateMessageFieldFrame.rippleProgress = 0
                                        privateRippleAnimator.restart()
                                    } else {
                                        privateRippleAnimator.stop()
                                        privateMessageFieldFrame.rippleProgress = 0
                                        if (peerKey.length > 0) {
                                            chatClient.indicatePrivateTyping(peerKey, false)
                                            privateTypingTimer.stop()
                                        }
                                    }
                                }
                                onTextChanged: {
                                    if (peerKey.length > 0) {
                                        window.privateDrafts[peerKey] = text
                                        if (text.length > 0) {
                                            chatClient.indicatePrivateTyping(peerKey, true)
                                            privateTypingTimer.restart()
                                        } else {
                                            chatClient.indicatePrivateTyping(peerKey, false)
                                            privateTypingTimer.stop()
                                        }
                                    }
                                }
                            }
                        }

                        ToolButton {
                            id: privateEmojiButton
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            padding: 0
                            focusPolicy: Qt.NoFocus
                            property color baseColor: window.palette.canvas
                            property color hoverColor: window.palette.surface
                            property color activeColor: window.palette.accentSoft
                            property color borderColor: window.palette.outline
                            text: "😊"
                            font.pixelSize: 20
                            onClicked: {
                                var pos = mapToItem(null, 0, height)
                                privateEmojiMenu.x = pos.x
                                privateEmojiMenu.y = pos.y
                                privateEmojiMenu.open()
                            }
                            background: Rectangle {
                                radius: 18
                                color: privateEmojiButton.down ? privateEmojiButton.activeColor : (privateEmojiButton.hovered ? privateEmojiButton.hoverColor : privateEmojiButton.baseColor)
                                border.width: 1
                                border.color: privateEmojiButton.down || privateEmojiButton.hovered ? window.palette.accent : privateEmojiButton.borderColor
                                Behavior on color {
                                    ColorAnimation { duration: 140; easing.type: Easing.OutQuad }
                                }
                                Behavior on border.color {
                                    ColorAnimation { duration: 140; easing.type: Easing.OutQuad }
                                }
                            }
                            contentItem: Text {
                                anchors.fill: parent
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                text: privateEmojiButton.text
                                color: window.palette.textPrimary
                                font.pixelSize: privateEmojiButton.font.pixelSize
                            }
                        }

                        ToolButton {
                            id: privateFileButton
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            padding: 0
                            focusPolicy: Qt.NoFocus
                            property color baseColor: window.palette.canvas
                            property color hoverColor: window.palette.surface
                            property color activeColor: window.palette.accentSoft
                            property color borderColor: window.palette.outline
                            text: "📎"
                            font.pixelSize: 18
                            enabled: peerKey.length > 0
                            onClicked: privateFileDialog.open()
                            background: Rectangle {
                                radius: 18
                                color: privateFileButton.down ? privateFileButton.activeColor : (privateFileButton.hovered ? privateFileButton.hoverColor : privateFileButton.baseColor)
                                border.width: 1
                                border.color: privateFileButton.down || privateFileButton.hovered ? window.palette.accent : privateFileButton.borderColor
                                Behavior on color {
                                    ColorAnimation { duration: 140; easing.type: Easing.OutQuad }
                                }
                                Behavior on border.color {
                                    ColorAnimation { duration: 140; easing.type: Easing.OutQuad }
                                }
                            }
                            contentItem: Text {
                                anchors.fill: parent
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                text: privateFileButton.text
                                color: window.palette.textPrimary
                                font.pixelSize: privateFileButton.font.pixelSize
                            }
                        }

                        Button {
                            id: sendButton
                            Layout.preferredWidth: 124
                            Layout.preferredHeight: 48
                            padding: 0
                            topPadding: 0
                            bottomPadding: 0
                            leftPadding: 0
                            rightPadding: 0
                            transformOrigin: Item.Center
                            scale: down ? 0.95 : (hovered ? 1.05 : 1.0)
                            Behavior on scale {
                                NumberAnimation {
                                    duration: 160
                                    easing.type: Easing.OutQuad
                                }
                            }
                            background: Rectangle {
                                radius: 22
                                border.width: 0
                                gradient: Gradient {
                                    GradientStop { position: 0.0; color: palette.accentBold }
                                    GradientStop { position: 1.0; color: palette.accent }
                                }
                                Rectangle {
                                    anchors.fill: parent
                                    radius: parent.radius
                                    gradient: Gradient {
                                        GradientStop { position: 0.0; color: "#40FFFFFF" }
                                        GradientStop { position: 1.0; color: "#00FFFFFF" }
                                    }
                                    opacity: sendButton.down ? 0.55 : (sendButton.hovered ? 0.28 : 0.0)
                                    Behavior on opacity {
                                        NumberAnimation {
                                            duration: 160
                                            easing.type: Easing.OutQuad
                                        }
                                    }
                                }
                            }
                            contentItem: Text {
                                anchors.centerIn: parent
                                text: "Send"
                                color: palette.panel
                                font.pixelSize: 15
                                font.bold: true
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            onClicked: {
                                if (peerKey.length === 0) {
                                    return
                                }
                                var text = messageField.text.trim()
                                var pendingPath = pendingFile && pendingFile.path ? pendingFile.path : ""
                                if (text.length === 0 && pendingPath.length === 0) {
                                    chatClient.sendPrivateMessageWithAttachment(peerKey, "", "")
                                    return
                                }
                                chatClient.sendPrivateMessageWithAttachment(peerKey, text, pendingPath)
                                if (text.length > 0 || pendingPath.length > 0) {
                                    messageField.text = ""
                                    window.privateDrafts[peerKey] = ""
                                    pendingFile = null
                                    window.setPrivatePendingFile(peerKey, null)
                                    window.setConversationUnread(peerKey, false)
                                    chatClient.indicatePrivateTyping(peerKey, false)
                                    privateTypingTimer.stop()
                                    scrollArea.scrollToEnd(true)
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        visible: pendingFile !== null
                        radius: 14
                        color: window.palette.canvas
                        border.color: window.palette.outline
                        border.width: 1
                        implicitHeight: privateAttachmentRow.implicitHeight + 16

                        RowLayout {
                            id: privateAttachmentRow
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 12

                            Text {
                                text: "📎"
                                font.pixelSize: 20
                                color: window.palette.accent
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2

                                Text {
                                    text: pendingFile ? pendingFile.name : ""
                                    color: window.palette.textPrimary
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Text {
                                    text: pendingFile ? window.formatFileSize(pendingFile.size) : ""
                                    color: window.palette.textSecondary
                                    font.pixelSize: 11
                                    Layout.fillWidth: true
                                }
                            }

                            ToolButton {
                                Layout.preferredWidth: 28
                                Layout.preferredHeight: 28
                                padding: 0
                                text: "\u2715"
                                onClicked: {
                                    pendingFile = null
                                    window.setPrivatePendingFile(peerKey, null)
                                    messageField.forceActiveFocus()
                                }
                                background: Rectangle {
                                    radius: 12
                                    color: window.palette.surface
                                    border.color: window.palette.outline
                                    border.width: 1
                                }
                                contentItem: Text {
                                    anchors.centerIn: parent
                                    text: parent.text
                                    color: window.palette.textSecondary
                                    font.pixelSize: 12
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Component {
        id: messageDelegateComponent

        Column {
            id: messageContainer
            width: parent ? parent.width : 0
            spacing: 6
            property bool isFirst: index === 0
            property bool isSystem: model.user === "System"
            opacity: 0
            scale: 0.97
            transformOrigin: Item.TopLeft

            Component.onCompleted: entryAnimation.start()

            ParallelAnimation {
                id: entryAnimation
                running: false
                PropertyAnimation {
                    target: messageContainer
                    property: "opacity"
                    to: 1
                    duration: 220
                    easing.type: Easing.OutQuad
                }
                PropertyAnimation {
                    target: messageContainer
                    property: "scale"
                    to: 1
                    duration: 220
                    easing.type: Easing.OutBack
                }
            }

            Rectangle {
                width: parent.width
                height: isFirst ? 0 : 1
                color: palette.outline
                opacity: 0.18
            }

            Text {
                text: model.displayContext !== undefined ? model.displayContext : (model.isPrivate ? model.user + " • private channel" : model.user)
                color: model.isPrivate ? palette.accent : palette.textSecondary
                font.pixelSize: 12
                font.bold: true
                Layout.alignment: Qt.AlignLeft
                topPadding: isFirst ? 2 : 0
            }

            Rectangle {
                id: messageBubble
                width: parent.width
                radius: 18
                color: messageContainer.isSystem ? palette.accentSoft : (model.isPrivate ? ((model.isOutgoing === true) ? palette.card : palette.accentSoft) : palette.card)
                border.width: 1
                property color baseBorder: messageContainer.isSystem ? palette.accent : (model.isPrivate ? ((model.isOutgoing === true) ? palette.canvas : palette.accent) : palette.canvas)
                property bool hasText: model.text && model.text.length > 0
                property bool hasFile: model.fileName && model.fileName.length > 0
                implicitHeight: bubbleContent.implicitHeight + 40
                border.color: baseBorder
                Behavior on border.color {
                    ColorAnimation {
                        duration: 260
                        easing.type: Easing.OutQuad
                    }
                }

                SequentialAnimation {
                    id: systemGlow
                    running: false
                    ColorAnimation {
                        target: messageBubble
                        property: "border.color"
                        to: palette.accentBold
                        duration: 120
                        easing.type: Easing.OutQuad
                    }
                    ColorAnimation {
                        target: messageBubble
                        property: "border.color"
                        to: messageBubble.baseBorder
                        duration: 420
                        easing.type: Easing.OutCubic
                    }
                }

                Component.onCompleted: {
                    if (messageContainer.isSystem) {
                        systemGlow.restart()
                    }
                }

                Column {
                    id: bubbleContent
                    width: parent.width - 40
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.leftMargin: 20
                    anchors.rightMargin: 20
                    anchors.top: parent.top
                    anchors.topMargin: 20
                    spacing: messageBubble.hasText && messageBubble.hasFile ? 12 : 6

                    Text {
                        visible: messageBubble.hasText
                        text: model.text
                        color: palette.textPrimary
                        wrapMode: Text.Wrap
                        width: parent.width
                        font.pixelSize: 15
                        lineHeight: 1.35
                    }

                    Rectangle {
                        width: parent.width
                        visible: messageBubble.hasFile
                        radius: 10
                        color: palette.surface
                        border.color: palette.outline
                        border.width: 1
                        implicitHeight: attachmentRow.implicitHeight + 16

                        RowLayout {
                            id: attachmentRow
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 12

                            Text {
                                text: "📎"
                                font.pixelSize: 20
                                color: palette.accent
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2

                                Text {
                                    text: model.fileName
                                    color: palette.textPrimary
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Text {
                                    text: window.formatFileSize(model.fileSize)
                                    color: palette.textSecondary
                                    font.pixelSize: 11
                                    Layout.fillWidth: true
                                }
                            }

                            // Save dialog for choosing download location
                            Platform.FileDialog {
                                id: saveAttachmentDialog
                                title: "Save attachment"
                                fileMode: Platform.FileDialog.SaveFile
                                nameFilters: ["All files (*)"]
                                property string payloadData: ""
                                onAccepted: {
                                    var chosen = selectedFile || file || currentFile
                                    if (!chosen || chosen.length === 0)
                                        return
                                    var result = chatClient.saveFileToPath(chosen, payloadData)
                                    if (result && result.length > 0)
                                        console.log("[QML] File saved to:", result)
                                }
                            }

                            Button {
                                text: "Download"
                                focusPolicy: Qt.NoFocus
                                onClicked: {
                                    if (!model.fileData || model.fileData.length === 0)
                                        return
                                    saveAttachmentDialog.payloadData = model.fileData
                                    // Suggest the original filename
                                    try { saveAttachmentDialog.currentFile = model.fileName } catch(e) {}
                                    saveAttachmentDialog.open()
                                }
                            }
                        }
                    }

                    RowLayout {
                        width: parent.width
                        Layout.fillWidth: true
                        
                        // Left-aligned timestamp
                        Text {
                            color: palette.textSecondary
                            font.pixelSize: 11
                            text: model.timestamp ? model.timestamp : ""
                            Layout.alignment: Qt.AlignLeft
                        }

                        // Right-aligned private status
                        Text {
                            Layout.fillWidth: true
                            color: palette.textSecondary
                            font.pixelSize: 11
                            horizontalAlignment: Text.AlignRight
                            text: {
                                if (!model.isPrivate || model.isOutgoing !== true) {
                                    return ""
                                }
                                var state = model.status ? model.status.toLowerCase() : ""
                                if (state === "seen") {
                                    return "\u2713\u2713 Seen"
                                }
                                if (state === "delivered") {
                                    return "\u2713 Delivered"
                                }
                                if (state === "sent") {
                                    return "\u2713 Sent"
                                }
                                return ""
                            }
                            visible: text && text.length > 0
                        }
                    }
                }
            }
        }
    }

    Component {
        id: userDelegateComponent

        Rectangle {
            id: userCard
            width: usersView.width
            height: 56
            radius: 20
            color: hovered ? palette.accentSoft : palette.card
            border.color: hovered ? palette.accent : palette.outline
            border.width: hovered ? 1 : 0
            transformOrigin: Item.Center
            scale: hovered ? 1.04 : 1.0

            Behavior on scale {
                NumberAnimation {
                    duration: 140
                    easing.type: Easing.OutQuad
                }
            }

            Behavior on color {
                ColorAnimation {
                    duration: 140
                    easing.type: Easing.OutQuad
                }
            }

            Behavior on border.color {
                ColorAnimation {
                    duration: 140
                    easing.type: Easing.OutQuad
                }
            }

            property bool hovered: mouseArea.containsMouse

            MouseArea {
                id: mouseArea
                anchors.fill: parent
                hoverEnabled: true
                onClicked: window.openPrivateConversation(name)
            }

            RowLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 12

                Rectangle {
                    id: avatarBadge
                    Layout.preferredWidth: 30
                    Layout.preferredHeight: 30
                    radius: 15
                    property var avatarColors: window.avatarGradientFor(name)
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: avatarBadge.avatarColors.top }
                        GradientStop { position: 1.0; color: avatarBadge.avatarColors.bottom }
                    }
                    border.color: "#60FFFFFF"
                    border.width: 1
                    scale: 1
                    transformOrigin: Item.Center
                    property real sheenOffset: -0.6

                    Rectangle {
                        anchors.fill: parent
                        radius: parent.radius
                        color: "transparent"
                        border.width: 0
                        gradient: Gradient {
                            GradientStop {
                                position: Math.max(0.0, Math.min(1.0, avatarBadge.sheenOffset - 0.2))
                                color: "#00FFFFFF"
                            }
                            GradientStop {
                                position: Math.max(0.0, Math.min(1.0, avatarBadge.sheenOffset))
                                color: "#55FFFFFF"
                            }
                            GradientStop {
                                position: Math.max(0.0, Math.min(1.0, avatarBadge.sheenOffset + 0.2))
                                color: "#00FFFFFF"
                            }
                        }
                        opacity: 0.7
                    }

                    SequentialAnimation {
                        id: avatarPulse
                        loops: Animation.Infinite
                        running: true
                        NumberAnimation {
                            target: avatarBadge
                            property: "scale"
                            to: 1.08
                            duration: 1100
                            easing.type: Easing.InOutSine
                        }
                        NumberAnimation {
                            target: avatarBadge
                            property: "scale"
                            to: 1.0
                            duration: 1100
                            easing.type: Easing.InOutSine
                        }
                    }

                    NumberAnimation on sheenOffset {
                        loops: Animation.Infinite
                        from: -0.6
                        to: 1.6
                        duration: 4200
                        easing.type: Easing.InOutSine
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2

                    Text {
                        text: name
                        color: palette.textPrimary
                        font.pixelSize: 14
                        font.bold: true
                    }

                    Text {
                        text: "Invite to private line"
                        color: palette.textSecondary
                        font.pixelSize: 11
                    }
                }
            }
        }
    }

    // --- BACKEND CONNECTIONS ---
    Connections {
        target: chatClient

        function onMessageReceived(username, message, file) {
            console.log("[QML] onMessageReceived user=", username,
                        "textLen=", message ? message.length : 0,
                        "file?=", !!file,
                        "fileName=", (file && file.name) ? file.name : "",
                        "size=", (file && file.size) ? file.size : 0)
            if (file && (file.is_private === true || (file.recipient && file.recipient.length > 0))) {
                // This is a private file payload; it will be handled by private handlers
                return
            }
            messagesModel.append({
                "user": username,
                "text": message,
                "isPrivate": false,
                "fileName": file && file.name ? file.name : "",
                "fileMime": file && file.mime ? file.mime : "",
                "fileData": file && file.data ? file.data : "",
                "fileSize": file && file.size ? Number(file.size) : 0,
                "timestamp": formatTimestamp("")
            })
            window.updatePublicTyping(username, false)
            var idx = window.conversationIndex("public")
            var isActive = idx === conversationTabBar.currentIndex
            if (!isActive) {
                window.setConversationUnread("public", true)
            }
            var page = window.conversationPages["public"]
            if (page && page.scrollToEnd) {
                page.scrollToEnd(isActive)
            }
        }

        function onGeneralHistoryReceived(history) {
            messagesModel.clear()
            if (!history) {
                return
            }
            for (var i = 0; i < history.length; ++i) {
                var entry = history[i]
                if (!entry) {
                    continue
                }
                messagesModel.append({
                    "user": entry.username ? entry.username : "Unknown",
                    "text": entry.message ? entry.message : "",
                    "isPrivate": false,
                    "fileName": entry.file && entry.file.name ? entry.file.name : "",
                    "fileMime": entry.file && entry.file.mime ? entry.file.mime : "",
                    "fileData": entry.file && entry.file.data ? entry.file.data : "",
                    "fileSize": entry.file && entry.file.size ? Number(entry.file.size) : 0,
                    "timestamp": formatTimestamp(entry.timestamp ? entry.timestamp : "")
                })
            }
            window.setConversationUnread("public", false)
            var page = window.conversationPages["public"]
            if (page && page.scrollToEnd) {
                page.scrollToEnd(false)
            }
        }

        function onPrivateMessageReceived(sender, recipient, message, messageId, status, file) {
            var currentUser = chatClient.username
            var isOutgoing = sender === currentUser
            var peer = isOutgoing ? recipient : sender
            if (!peer || peer.length === 0) {
                return
            }
            window.appendPrivateMessage(peer, sender, message, isOutgoing, messageId, status, file, formatTimestamp(""))
            if (!isOutgoing) {
                window.updatePrivateTyping(sender, false)
            }
        }

        function onPrivateMessageSent(sender, recipient, message, messageId, status, file) {
            var peer = recipient
            if (!peer || peer.length === 0) {
                return
            }
            window.appendPrivateMessage(peer, sender, message, true, messageId, status, file, formatTimestamp(""))
            window.setConversationUnread(peer, false)
        }

        function onPrivateMessageReceivedEx(sender, recipient, message, messageId, status, file, timestamp) {
            var currentUser = chatClient.username
            var isOutgoing = sender === currentUser
            var peer = isOutgoing ? recipient : sender
            if (!peer || peer.length === 0) {
                return
            }
            window.appendPrivateMessage(peer, sender, message, isOutgoing, messageId, status, file, formatTimestamp(timestamp))
            if (!isOutgoing) {
                window.updatePrivateTyping(sender, false)
            }
        }

        function onPrivateMessageSentEx(sender, recipient, message, messageId, status, file, timestamp) {
            var peer = recipient
            if (!peer || peer.length === 0) {
                return
            }
            window.appendPrivateMessage(peer, sender, message, true, messageId, status, file, formatTimestamp(timestamp))
            window.setConversationUnread(peer, false)
        }

        function onPrivateMessageRead(messageId) {
            window.setPrivateMessageStatusById(messageId, "seen")
        }

        function onPublicTypingReceived(username, isTyping) {
            window.updatePublicTyping(username, isTyping)
        }

        function onPrivateTypingReceived(username, isTyping) {
            window.updatePrivateTyping(username, isTyping)
        }

        function onUsersUpdated(users) {
            usersModel.clear()
            window.totalUserCount = users.length
            for (var i = 0; i < users.length; i++) {
                if (users[i] !== chatClient.username) {
                    usersModel.append({"name": users[i]})
                }
            }
            var filteredPublic = []
            for (var j = 0; j < window.publicTypingUsers.length; ++j) {
                var name = window.publicTypingUsers[j]
                if (users.indexOf(name) !== -1) {
                    filteredPublic.push(name)
                }
            }
            window.publicTypingUsers = filteredPublic
            window.publicTypingText = window.buildPublicTypingText(filteredPublic)
            var refreshedPrivate = {}
            for (var k = 0; k < users.length; ++k) {
                var user = users[k]
                if (window.privateTypingStates[user]) {
                    refreshedPrivate[user] = true
                }
            }
            window.privateTypingStates = refreshedPrivate
        }

        function onDisconnected() {
            messagesModel.clear()
            usersModel.clear()
            window.totalUserCount = 0
            window.resetPrivateConversations()
            messagesModel.append({
                "user": "System",
                "text": "You have been disconnected.",
                "isPrivate": false,
                "fileName": "",
                "fileMime": "",
                "fileData": "",
                "fileSize": 0
            })
            window.setConversationUnread("public", false)
            disconnectAnimation.restart()
        }

        function onErrorReceived(message) {
            messagesModel.append({
                "user": "System",
                "text": message,
                "isPrivate": false,
                "fileName": "",
                "fileMime": "",
                "fileData": "",
                "fileSize": 0
            })
            var idx = window.conversationIndex("public")
            var isActive = idx === conversationTabBar.currentIndex
            if (!isActive) {
                window.setConversationUnread("public", true)
            }
            var page = window.conversationPages["public"]
            if (page && page.scrollToEnd) {
                page.scrollToEnd(isActive)
            }
        }

        function onUsernameChanged(name) {
            usernameField.text = name
            if (name.length > 0) {
                window.focusActiveComposer()
            }
        }
    }
}
}
