import QtQuick 2.15
import QtQuick.Controls 2.15

ApplicationWindow {
    visible: true
    width: 700
    height: 500
    title: "Simple Chat Client"

    // Use a Column as the root layout to stack items vertically
    Column {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 10

        // Top bar (registration)
        Row {
            id: topRow
            width: parent.width
            spacing: 8
            height: 40

            TextField {
                id: usernameField
                placeholderText: "Enter username"
                width: 200
            }
            Button {
                text: "Register"
                onClicked: {
                    if (usernameField.text.length > 0) {
                        chatClient.register(usernameField.text)
                        usernameField.enabled = false
                    }
                }
            }
            Button { text: "Disconnect"; onClicked: chatClient.disconnect() }
        }

        // Main content area
        Row {
            id: mainRow
            width: parent.width
            // The Column layout handles height, so this fills the remaining space
            height: parent.height - topRow.height - parent.spacing
            spacing: 12

            // Chat messages and input
            Rectangle {
                width: parent.width * 0.7 - (mainRow.spacing / 2)
                height: parent.height
                color: "#fafafa"
                border.color: "#cccccc"
                radius: 4

                ListView {
                    id: messagesView
                    model: messagesModel
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: inputRow.top
                    anchors.margins: 8
                    clip: true
                    delegate: Row {
                        spacing: 8
                        Text { text: model.user + ":"; font.bold: true }
                        Text { text: model.text; wrapMode: Text.Wrap }
                    }
                }

                Row {
                    id: inputRow
                    spacing: 8
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.margins: 8
                    height: 40

                    TextField {
                        id: messageField
                        placeholderText: "Type a message"
                        width: parent.width - sendBtn.width - 20
                        anchors.verticalCenter: parent.verticalCenter
                        onAccepted: sendBtn.clicked()
                    }
                    Button {
                        id: sendBtn
                        text: "Send"
                        onClicked: {
                            if (messageField.text.length > 0) {
                                chatClient.sendMessage(messageField.text)
                                messageField.text = ""
                            }
                        }
                    }
                }
            }

            // Active users list
            Rectangle {
                width: parent.width * 0.3 - (mainRow.spacing / 2)
                height: parent.height
                color: "#ffffff"
                border.color: "#cccccc"
                radius: 4

                Column {
                    id: usersColumn
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 6

                    Text {
                        id: usersHeader
                        text: "Active users"
                        font.pixelSize: 16
                        font.bold: true
                    }

                    ListView {
                        id: usersView
                        model: usersModel
                        width: parent.width
                        height: parent.height - usersHeader.height - parent.spacing
                        clip: true
                        delegate: Text {
                            text: name
                            leftPadding: 4
                        }
                    }
                }
            }
        }
    }

    ListModel { id: messagesModel }
    ListModel { id: usersModel }

    Connections {
        target: chatClient

        function onMessageReceived(username, message) {
            messagesModel.append({"user": username, "text": message})
        }

        function onUsersUpdated(users) {
            usersModel.clear()
            for (var i = 0; i < users.length; i++) {
                usersModel.append({"name": users[i]})
            }
        }
    }
}