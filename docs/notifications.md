# Telegram notifications

TDM can send a Telegram alert when it successfully claims a drop. Notifications cover
claims found through live events and through startup or later inventory checks.

## Set up a bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram and create a bot. Keep the
   bot token private.
2. Start a conversation with your new bot and send it a message.
3. Find your chat ID using the Telegram setup instructions in TDM's **Help** tab.
4. In **Settings → Telegram Notifications**, enter the bot token and chat ID.
5. Select **Test Connection**. A successful test sends a message and saves the
   settings. **Save Settings** saves without sending a test message.

If testing or saving fails, the dashboard displays an error. Check the token, chat ID,
whether you have started the conversation, and the miner's access to Telegram before
trying again. Do not post the token or a URL containing it in a support request.

## Update or disable notifications

The token is stored on the miner and is not returned to the browser. Leave the token
field blank to reuse the saved token when testing or changing the chat ID. Enter a
new token when you intend to replace it.

To disable notifications, clear the chat ID and select **Save Settings**. To enable
them again, enter the chat ID and save or test the connection.

## Delivery behavior

Repeated events for an already claimed drop do not send duplicate alerts. Telegram
delivery failures do not undo a successful Twitch claim, and failed notifications
are not retried. Check the **History** tab or your Twitch inventory if an alert is
missing; the absence of a Telegram message does not mean a claim failed.

[All guides](README.md) · [Using the dashboard](usage.md)
