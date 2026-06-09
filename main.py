import csv
import re
import threading
import time
import tkinter as tk
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import pyperclip
except ImportError:  # pragma: no cover - handled in the GUI at runtime
    pyperclip = None

try:
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError:  # pragma: no cover - handled in the GUI at runtime
    webdriver = None
    TimeoutException = WebDriverException = Exception
    ActionChains = Options = By = Keys = EC = WebDriverWait = None


PLACEHOLDER_PATTERN = re.compile(r"\[([A-Za-z0-9_ -]+)\]")


@dataclass
class Contact:
    row_number: int
    values: dict[str, str]


@dataclass
class SkippedContact:
    row_number: int
    phone: str
    name: str
    reason: str


def normalize_header(header: str) -> str:
    return header.strip()


def clean_phone_number(phone: str, default_country_code: str) -> str:
    phone = str(phone or "").strip()
    phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("00"):
        phone = phone[2:]
    if default_country_code and len(phone) <= 10:
        phone = f"{default_country_code.strip().lstrip('+')}{phone.lstrip('0')}"
    return re.sub(r"\D", "", phone)


def render_template(template: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return values.get(key, match.group(0))

    return PLACEHOLDER_PATTERN.sub(replace, template)


class WhatsAppBulkSenderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("WhatsApp Bulk Message Sender")
        self.geometry("1060x720")
        self.minsize(920, 620)

        self.csv_path: Path | None = None
        self.headers: list[str] = []
        self.contacts: list[Contact] = []
        self.skipped_contacts: list[SkippedContact] = []
        self.worker: threading.Thread | None = None
        self.stop_requested = threading.Event()

        self.csv_path_var = tk.StringVar(value="No CSV selected")
        self.phone_field_var = tk.StringVar()
        self.country_code_var = tk.StringVar(value="91")
        self.delay_var = tk.IntVar(value=12)
        self.initial_wait_var = tk.IntVar(value=18)
        self.limit_var = tk.StringVar(value="")
        self.dry_run_var = tk.BooleanVar(value=False)
        self.consent_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Load a CSV to begin.")

        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self, padding=(14, 12))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Button(top, text="Choose CSV", command=self.choose_csv).grid(row=0, column=0, padx=(0, 10))
        ttk.Label(top, textvariable=self.csv_path_var).grid(row=0, column=1, sticky="ew")

        settings = ttk.Frame(self, padding=(14, 0, 14, 10))
        settings.grid(row=1, column=0, sticky="ew")
        for column in range(10):
            settings.columnconfigure(column, weight=0)
        settings.columnconfigure(9, weight=1)

        ttk.Label(settings, text="Phone field").grid(row=0, column=0, sticky="w")
        self.phone_combo = ttk.Combobox(settings, textvariable=self.phone_field_var, state="readonly", width=18)
        self.phone_combo.grid(row=0, column=1, padx=(6, 18), sticky="w")

        ttk.Label(settings, text="Default country code").grid(row=0, column=2, sticky="w")
        ttk.Entry(settings, textvariable=self.country_code_var, width=8).grid(row=0, column=3, padx=(6, 18))

        ttk.Label(settings, text="Delay seconds").grid(row=0, column=4, sticky="w")
        ttk.Spinbox(settings, from_=8, to=120, textvariable=self.delay_var, width=6).grid(
            row=0, column=5, padx=(6, 18)
        )

        ttk.Label(settings, text="First wait").grid(row=0, column=6, sticky="w")
        ttk.Spinbox(settings, from_=10, to=90, textvariable=self.initial_wait_var, width=6).grid(
            row=0, column=7, padx=(6, 18)
        )

        ttk.Label(settings, text="Limit").grid(row=0, column=8, sticky="w")
        ttk.Entry(settings, textvariable=self.limit_var, width=8).grid(row=0, column=9, padx=(6, 0), sticky="w")

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 10))

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=2)
        body.add(right, weight=3)

        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="CSV contacts").grid(row=0, column=0, sticky="w", pady=(0, 6))
        table_frame = ttk.Frame(left)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.table = ttk.Treeview(table_frame, show="headings")
        self.table.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.table.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.table.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        right.rowconfigure(1, weight=1)
        right.rowconfigure(3, weight=1)
        right.columnconfigure(0, weight=1)

        ttk.Label(right, text="Message template").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.message_text = tk.Text(right, height=9, wrap="word", undo=True)
        self.message_text.grid(row=1, column=0, sticky="nsew")
        self.message_text.insert("1.0", "Hello [name], this is a message from [company].")
        self.message_text.bind("<KeyRelease>", lambda _event: self.refresh_preview())

        helper = ttk.Frame(right)
        helper.grid(row=2, column=0, sticky="ew", pady=8)
        helper.columnconfigure(1, weight=1)
        ttk.Button(helper, text="Insert field", command=self.insert_selected_field).grid(row=0, column=0, sticky="w")
        self.field_combo = ttk.Combobox(helper, state="readonly", width=22)
        self.field_combo.grid(row=0, column=1, padx=(8, 0), sticky="w")
        ttk.Button(helper, text="Preview", command=self.refresh_preview).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(right, text="Personalized preview").grid(row=3, column=0, sticky="sw", pady=(4, 6))
        self.preview_text = tk.Text(right, height=8, wrap="word", state="disabled")
        self.preview_text.grid(row=4, column=0, sticky="nsew")

        bottom = ttk.Frame(self, padding=(14, 0, 14, 14))
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.columnconfigure(2, weight=1)

        ttk.Checkbutton(
            bottom,
            text="Dry run only (preview; no browser opens)",
            variable=self.dry_run_var,
            command=self._sync_send_button_label,
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            bottom,
            text="I confirm these recipients opted in to receive this message",
            variable=self.consent_var,
        ).grid(row=0, column=1, padx=(18, 0), sticky="w")

        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        actions = ttk.Frame(bottom)
        actions.grid(row=0, column=2, rowspan=2, sticky="e")
        self.send_button = ttk.Button(actions, text="Start Sending", command=self.start_sending)
        self.send_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button = ttk.Button(actions, text="Stop", command=self.stop_sending, state="disabled")
        self.stop_button.grid(row=0, column=1)

        ttk.Label(bottom, textvariable=self.status_var).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self._sync_send_button_label()

    def choose_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose contacts CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            self.load_csv(Path(path))
        except Exception as exc:
            messagebox.showerror("Could not load CSV", str(exc))

    def load_csv(self, path: Path) -> None:
        with path.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            if not reader.fieldnames:
                raise ValueError("The CSV must include a header row.")

            self.headers = [normalize_header(header) for header in reader.fieldnames]
            self.contacts = []
            for index, row in enumerate(reader, start=2):
                values = {normalize_header(key): (value or "").strip() for key, value in row.items() if key}
                if any(values.values()):
                    self.contacts.append(Contact(row_number=index, values=values))

        if not self.contacts:
            raise ValueError("No contact rows were found in the CSV.")

        self.csv_path = path
        self.csv_path_var.set(str(path))
        self._populate_table()
        self._populate_fields()
        self.refresh_preview()
        self.status_var.set(f"Loaded {len(self.contacts)} contacts.")

    def _populate_table(self) -> None:
        self.table.delete(*self.table.get_children())
        self.table["columns"] = self.headers
        for header in self.headers:
            self.table.heading(header, text=header)
            self.table.column(header, width=max(110, len(header) * 12), stretch=True)

        for contact in self.contacts[:500]:
            self.table.insert("", tk.END, values=[contact.values.get(header, "") for header in self.headers])

    def _populate_fields(self) -> None:
        self.field_combo["values"] = self.headers
        self.phone_combo["values"] = self.headers
        if self.headers:
            self.field_combo.set(self.headers[0])

        likely_phone = next(
            (
                header
                for header in self.headers
                if header.lower().replace(" ", "_") in {"phone", "phone_number", "mobile", "mobile_number", "number"}
            ),
            self.headers[0],
        )
        self.phone_field_var.set(likely_phone)

    def insert_selected_field(self) -> None:
        field = self.field_combo.get()
        if not field:
            return
        self.message_text.insert(tk.INSERT, f"[{field}]")
        self.refresh_preview()

    def refresh_preview(self) -> None:
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", tk.END)
        if not self.contacts:
            self.preview_text.insert("1.0", "Load a CSV to see previews.")
        else:
            template = self.get_template()
            previews = []
            for contact in self.contacts[:5]:
                message = render_template(template, contact.values)
                previews.append(f"Row {contact.row_number}:\n{message}")
            self.preview_text.insert("1.0", "\n\n".join(previews))
        self.preview_text.configure(state="disabled")

    def get_template(self) -> str:
        return self.message_text.get("1.0", tk.END).strip()

    def start_sending(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if (webdriver is None or pyperclip is None) and not self.dry_run_var.get():
            messagebox.showerror(
                "Missing dependency",
                "Install dependencies first: pip install -r requirements.txt",
            )
            return
        if not self.contacts:
            messagebox.showwarning("No CSV", "Please load a CSV file first.")
            return
        if not self.phone_field_var.get():
            messagebox.showwarning("Phone field missing", "Please select the phone number field.")
            return
        if not self.get_template():
            messagebox.showwarning("Message missing", "Please enter a message template.")
            return
        if not self.dry_run_var.get() and not self.consent_var.get():
            messagebox.showwarning("Consent required", "Confirm that recipients opted in before sending.")
            return

        limit = self._get_limit()
        if limit is None:
            return

        self.stop_requested.clear()
        self.send_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.configure(value=0, maximum=min(limit or len(self.contacts), len(self.contacts)))
        if self.dry_run_var.get():
            self.status_var.set("Dry run started. This will not open Chrome or send WhatsApp messages.")
        else:
            self.status_var.set("Live sending started. Chrome should open in a moment.")
        self.worker = threading.Thread(target=self._send_messages, args=(limit,), daemon=True)
        self.worker.start()

    def _get_limit(self) -> int | None:
        raw = self.limit_var.get().strip()
        if not raw:
            return 0
        try:
            limit = int(raw)
        except ValueError:
            messagebox.showwarning("Invalid limit", "Limit must be empty or a whole number.")
            return None
        if limit < 1:
            messagebox.showwarning("Invalid limit", "Limit must be at least 1.")
            return None
        return limit

    def stop_sending(self) -> None:
        self.stop_requested.set()
        self.status_var.set("Stopping after the current contact...")

    def _send_messages(self, limit: int) -> None:
        contacts = self.contacts[: limit or None]
        phone_field = self.phone_field_var.get()
        template = self.get_template()
        delay_seconds = max(8, int(self.delay_var.get()))
        first_wait = max(10, int(self.initial_wait_var.get()))
        default_country_code = self.country_code_var.get()
        dry_run = self.dry_run_var.get()
        sent_count = 0
        skipped_count = 0
        seen_numbers: dict[str, int] = {}
        driver = None
        failure_message = ""
        self.skipped_contacts = []

        try:
            if not dry_run:
                self._set_status("Starting Chrome for WhatsApp Web...")
                driver = self._create_driver()

            for index, contact in enumerate(contacts, start=1):
                if self.stop_requested.is_set():
                    break

                phone = clean_phone_number(contact.values.get(phone_field, ""), default_country_code)
                message = render_template(template, contact.values)
                if not phone:
                    skipped_count += 1
                    self._record_skipped(contact, phone, "missing phone number")
                    self._set_progress(index)
                    continue
                if phone in seen_numbers:
                    skipped_count += 1
                    self._record_skipped(
                        contact,
                        phone,
                        f"duplicate phone number; first seen at row {seen_numbers[phone]}",
                    )
                    self._set_progress(index)
                    continue
                seen_numbers[phone] = contact.row_number

                if dry_run:
                    self._set_status(f"Dry run row {contact.row_number}: {phone} -> {message[:80]}")
                    sent_count += 1
                else:
                    base_wait = first_wait if sent_count == 0 else delay_seconds
                    sent_successfully, skip_reason = self._send_contact_with_retries(
                        driver,
                        contact.row_number,
                        phone,
                        message,
                        base_wait,
                    )
                    if sent_successfully:
                        sent_count += 1
                    elif not self.stop_requested.is_set():
                        skipped_count += 1
                        self._record_skipped(contact, phone, skip_reason)

                self._set_progress(index)
                if index < len(contacts) and not dry_run:
                    self._sleep_with_stop(delay_seconds)
        except WebDriverException as exc:
            failure_message = f"Browser automation failed: {exc}"
        except Exception as exc:
            failure_message = f"Sending failed: {exc}"
        finally:
            stopped = self.stop_requested.is_set()
            if failure_message:
                summary = f"{failure_message} Processed {sent_count}, skipped {skipped_count}."
            elif dry_run:
                summary = (
                    f"{'Stopped dry run.' if stopped else 'Dry run complete.'} "
                    f"Previewed {sent_count}, skipped {skipped_count}. No messages were sent."
                )
            else:
                summary = f"{'Stopped.' if stopped else 'Finished sending.'} Sent {sent_count}, skipped {skipped_count}."
            skipped_report = self._write_skipped_report()
            if skipped_report:
                summary = f"{summary} Skipped report: {skipped_report}"
            self.after(0, self._finish_sending, summary)

    def _create_driver(self):
        options = Options()
        profile_dir = Path.cwd() / ".whatsapp_chrome_profile"
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--disable-notifications")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        return webdriver.Chrome(options=options)

    def _send_contact_with_retries(
        self,
        driver,
        row_number: int,
        phone: str,
        message: str,
        base_wait_seconds: int,
    ) -> tuple[bool, str]:
        last_error = None
        for attempt in range(1, 4):
            if self.stop_requested.is_set():
                return False, "stopped by user"

            wait_seconds = max(base_wait_seconds, 30) + ((attempt - 1) * 30)
            self._set_status(
                f"Opening WhatsApp for row {row_number}: {phone} "
                f"(attempt {attempt}/3, timeout {wait_seconds}s)"
            )

            try:
                self._open_and_send(driver, phone, message, wait_seconds)
                return True, ""
            except ValueError as exc:
                self._set_status(f"Skipped row {row_number}: {exc}")
                return False, str(exc)
            except TimeoutException as exc:
                last_error = exc
                if attempt < 3:
                    self._set_status(f"Row {row_number} timed out. Retrying with longer wait...")
                    self._sleep_with_stop(3)
            except WebDriverException as exc:
                last_error = exc
                if attempt < 3:
                    self._set_status(f"Row {row_number} browser automation failed. Retrying...")
                    self._sleep_with_stop(3)

        self._set_status(f"Skipped row {row_number} after 3 tries: {last_error}")
        return False, f"failed after 3 tries: {last_error}"

    def _open_and_send(self, driver, phone: str, message: str, wait_seconds: int) -> None:
        try:
            self._send_message_alright_style(driver, phone, message, wait_seconds)
        except TimeoutException:
            self._open_with_prefilled_text_and_send(driver, phone, message, wait_seconds + 30)

    def _send_message_alright_style(self, driver, phone: str, message: str, wait_seconds: int) -> None:
        url = f"https://web.whatsapp.com/send?phone={phone}&text&type=phone_number&app_absent=1"
        driver.get(url)
        message_box = self._wait_for_message_box(driver, wait_seconds)
        if message_box is None or self.stop_requested.is_set():
            return

        self._type_message(driver, message_box, message)
        self._send_with_enter(driver, message_box)

    def _open_with_prefilled_text_and_send(self, driver, phone: str, message: str, wait_seconds: int) -> None:
        encoded_message = urllib.parse.quote(message)
        url = f"https://web.whatsapp.com/send?phone={phone}&text={encoded_message}"
        self._set_status(f"Trying WhatsApp URL message fallback with timeout {wait_seconds}s...")
        driver.get(url)
        self._send_current_message(driver, wait_seconds)

    def _fill_message_box(self, driver, message: str, timeout_seconds: int) -> None:
        message_box = self._wait_for_message_box(driver, timeout_seconds)
        if message_box is None:
            return
        if self.stop_requested.is_set():
            return

        self._type_message(driver, message_box, message)

    def _type_message(self, driver, message_box, message: str) -> None:
        message_box.click()
        message_box.send_keys(Keys.CONTROL, "a")
        message_box.send_keys(Keys.BACKSPACE)
        pyperclip.copy(message)
        message_box.send_keys(Keys.CONTROL, "v")
        time.sleep(0.5)

    def _send_with_enter(self, driver, message_box) -> None:
        try:
            message_box.send_keys(Keys.ENTER)
        except WebDriverException:
            ActionChains(driver).send_keys(Keys.ENTER).perform()

    def _wait_for_message_box(self, driver, timeout_seconds: int):
        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
            if self.stop_requested.is_set():
                return None

            unavailable_reason = self._get_unavailable_number_reason(driver)
            if unavailable_reason:
                raise ValueError(unavailable_reason)

            message_boxes = self._find_elements(
                driver,
                By.XPATH,
                "//footer//div[@contenteditable='true' and @role='textbox']",
            )
            if message_boxes:
                return message_boxes[-1]

            fallback_boxes = self._find_elements(
                driver,
                By.CSS_SELECTOR,
                "footer div[contenteditable='true'][role='textbox'], div[aria-label='Type a message']",
            )
            if fallback_boxes:
                return fallback_boxes[-1]

            login_qr = self._find_elements(driver, By.CSS_SELECTOR, "canvas, div[data-testid='qrcode']")
            if login_qr:
                self._set_status("Scan the WhatsApp Web QR code in Chrome, then keep this app running...")

            time.sleep(0.5)

        raise TimeoutException("Could not find WhatsApp message box. Make sure WhatsApp Web is logged in.")

    def _send_current_message(self, driver, timeout_seconds: int) -> None:
        deadline = time.time() + timeout_seconds
        last_error = None

        while time.time() < deadline:
            if self.stop_requested.is_set():
                return

            unavailable_reason = self._get_unavailable_number_reason(driver)
            if unavailable_reason:
                raise ValueError(unavailable_reason)

            send_icons = self._find_elements(
                driver,
                By.CSS_SELECTOR,
                "span[data-icon='send'], span[data-icon='wds-ic-send-filled'], span[data-testid='wds-ic-send-filled']",
            )
            if send_icons:
                try:
                    driver.execute_script("arguments[0].closest('button').click();", send_icons[0])
                    return
                except WebDriverException as exc:
                    last_error = exc

            send_buttons = self._find_elements(
                driver,
                By.CSS_SELECTOR,
                "button[aria-label='Send']:not([aria-disabled='true'])",
            )
            if send_buttons:
                try:
                    driver.execute_script("arguments[0].click();", send_buttons[0])
                    return
                except WebDriverException as exc:
                    last_error = exc

            message_boxes = self._find_elements(
                driver,
                By.XPATH,
                "//footer//div[@contenteditable='true' and @role='textbox']",
            )
            if message_boxes:
                try:
                    message_boxes[-1].send_keys(Keys.ENTER)
                    return
                except WebDriverException as exc:
                    last_error = exc

            time.sleep(0.5)

        if last_error:
            raise TimeoutException(f"Could not click WhatsApp Send button: {last_error}")
        raise TimeoutException("Could not find WhatsApp Send button. Try increasing First wait.")

    def _get_unavailable_number_reason(self, driver) -> str:
        unavailable_text_checks = [
            "Phone number shared via url is invalid",
            "phone number shared via url is invalid",
            "Phone number shared via URL is invalid",
            "not on WhatsApp",
            "isn't on WhatsApp",
            "is not on WhatsApp",
            "invalid phone number",
            "Invalid phone number",
        ]

        for text in unavailable_text_checks:
            matches = self._find_elements(driver, By.XPATH, f"//*[contains(text(), {self._xpath_literal(text)})]")
            if matches:
                return "number is invalid or not registered on WhatsApp."

        invite_buttons = self._find_elements(
            driver,
            By.XPATH,
            "//button[contains(., 'Invite') or contains(., 'invite')]",
        )
        if invite_buttons:
            return "number is not registered on WhatsApp."

        return ""

    def _xpath_literal(self, text: str) -> str:
        if "'" not in text:
            return f"'{text}'"
        if '"' not in text:
            return f'"{text}"'
        parts = text.split("'")
        return "concat(" + ', \"\'\", '.join(f"'{part}'" for part in parts) + ")"

    def _find_elements(self, driver, by, value: str):
        try:
            return driver.find_elements(by, value)
        except WebDriverException:
            return []

    def _record_skipped(self, contact: Contact, phone: str, reason: str) -> None:
        name = self._contact_display_name(contact)
        self.skipped_contacts.append(
            SkippedContact(
                row_number=contact.row_number,
                phone=phone or contact.values.get(self.phone_field_var.get(), ""),
                name=name,
                reason=reason,
            )
        )
        self._set_status(f"Skipped row {contact.row_number}: {phone or 'no phone'} - {reason}")

    def _contact_display_name(self, contact: Contact) -> str:
        for key in ("name", "Name", "full_name", "Full Name", "fullname"):
            value = contact.values.get(key)
            if value:
                return value
        return ""

    def _write_skipped_report(self) -> str:
        if not self.skipped_contacts:
            return ""

        report_path = Path.cwd() / "skipped_numbers.csv"
        with report_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=["row", "phone", "name", "reason"])
            writer.writeheader()
            for skipped in self.skipped_contacts:
                writer.writerow(
                    {
                        "row": skipped.row_number,
                        "phone": skipped.phone,
                        "name": skipped.name,
                        "reason": skipped.reason,
                    }
                )
        return str(report_path)

    def _sleep_with_stop(self, seconds: int) -> None:
        for _ in range(seconds * 10):
            if self.stop_requested.is_set():
                return
            time.sleep(0.1)

    def _set_progress(self, value: int) -> None:
        self.after(0, lambda: self.progress.configure(value=value))

    def _set_status(self, message: str) -> None:
        self.after(0, lambda: self.status_var.set(message))

    def _finish_sending(self, summary: str) -> None:
        self.send_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status_var.set(summary)
        self._sync_send_button_label()
        if self.skipped_contacts:
            preview_lines = [
                f"Row {item.row_number}: {item.phone or 'no phone'} - {item.reason}"
                for item in self.skipped_contacts[:20]
            ]
            extra = ""
            if len(self.skipped_contacts) > 20:
                extra = f"\n\nAnd {len(self.skipped_contacts) - 20} more. See skipped_numbers.csv."
            messagebox.showinfo("Skipped numbers", "\n".join(preview_lines) + extra)

    def _sync_send_button_label(self) -> None:
        if not hasattr(self, "send_button"):
            return
        if self.dry_run_var.get():
            self.send_button.configure(text="Start Dry Run")
        else:
            self.send_button.configure(text="Start Sending")


if __name__ == "__main__":
    app = WhatsAppBulkSenderApp()
    app.mainloop()
