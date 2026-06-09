# WhatsApp Bulk Message Sender

A small Tkinter desktop app that loads contacts from a CSV file, personalizes a message with fields such as `[name]`, and sends messages through WhatsApp Web.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## CSV format

Use a header row. Any column can be used in the message template.

```csv
name,phone,company
Alex,+911234567890,Acme
Priya,9876543210,Contoso
```

Templates use square brackets:

```text
Hello [name], this is a quick update from [company].
```

## Notes

- Chrome will open with a dedicated WhatsApp profile. Scan the WhatsApp Web QR code the first time.
- Sending uses an `alright`-style WhatsApp Web flow: open the number, wait for the textbox, paste the full message, then press Enter.
- Enable `Dry run only` when you want to preview without opening Chrome or sending messages.
- Only send messages to recipients who opted in.
- The app retries each contact up to 3 times with longer waits, then skips that contact if WhatsApp Web still is not ready.
- Numbers that WhatsApp reports as invalid or not registered are skipped automatically.
- Skipped rows are shown after the run and saved to `skipped_numbers.csv`.
- Duplicate phone numbers are skipped after the first occurrence.
- If the message opens but does not send, increase `First wait` to 45-60 seconds and make sure WhatsApp Web is fully logged in.
- Use a delay of at least 8-12 seconds between messages.

## Troubleshooting

If you see `Could not find WhatsApp message box` or `Could not find WhatsApp Send button`:

- Set `Limit` to `1` while testing.
- Increase `First wait` to `45` or `60`.
- Make sure the Chrome window opened by the app is logged into WhatsApp Web.
- Keep the Chrome window open while scanning the QR code; the app will wait and retry.
- If WhatsApp Web shows an invalid number message, check that your CSV phone numbers include the correct country code or set `Default country code`.
