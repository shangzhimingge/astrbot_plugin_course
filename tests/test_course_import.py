import ast
import importlib
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from icalendar import Calendar

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "_course_test_package"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]


class ComponentFile:
    def __init__(self, name, path):
        self.name = name
        self.get_file = AsyncMock(return_value=str(path))


class ComponentImage:
    file = url = None

    def __init__(self, path):
        self.convert_to_file_path = AsyncMock(return_value=str(path))


class Chain:
    def message(self, text):
        self.text = text
        return self


class Star:
    name = "test_course"

    def __init__(self, context):
        pass


def decorator(*args, **kwargs):
    return lambda function: function


def load_plugin():
    def module(name, **values):
        stub = ModuleType(name)
        stub.__dict__.update(values)
        return stub

    logger = Mock()
    modules = {
        PACKAGE: package,
        "astrbot": module("astrbot"),
        "astrbot.api": module("astrbot.api", logger=logger),
        "astrbot.api.event": module(
            "astrbot.api.event", AstrMessageEvent=object, MessageChain=Chain,
            filter=SimpleNamespace(command=decorator),
        ),
        "astrbot.api.message_components": module(
            "astrbot.api.message_components", File=ComponentFile, Image=ComponentImage,
        ),
        "astrbot.api.star": module(
            "astrbot.api.star", Context=object, Star=Star, StarTools=SimpleNamespace(),
            register=decorator,
        ),
        "astrbot.core.platform.message_session": module(
            "astrbot.core.platform.message_session",
            MessageSession=SimpleNamespace(from_str=lambda origin: origin),
        ),
        "astrbot.core.utils.astrbot_path": module(
            "astrbot.core.utils.astrbot_path", get_astrbot_temp_path=lambda: tempfile.gettempdir(),
        ),
        "astrbot.core.utils.session_waiter": module(
            "astrbot.core.utils.session_waiter", SessionController=object,
            SessionFilter=object, session_waiter=decorator,
        ),
    }
    with patch.dict(sys.modules, modules):
        plugin = importlib.import_module(f"{PACKAGE}.main")
        imported = importlib.import_module(f"{PACKAGE}.importer")
        storage = importlib.import_module(f"{PACKAGE}.storage")
        package_modules = {
            name: module for name, module in sys.modules.items()
            if name == PACKAGE or name.startswith(f"{PACKAGE}.")
        }
    sys.modules.update(package_modules)
    return plugin, imported, storage


plugin, importer, storage_module = load_plugin()
BIND = "bot:GroupMessage:100"
OTHER = "bot:GroupMessage:200"
PRIVATE = "bot:FriendMessage:001"
FOREIGN = "another-bot:GroupMessage:100"


def event(origin=BIND, owner="001"):
    platform, kind, group = origin.split(":", 2)
    return SimpleNamespace(
        unified_msg_origin=origin,
        get_sender_id=lambda: owner,
        get_platform_id=lambda: platform,
        get_group_id=lambda: group if kind == "GroupMessage" else "",
    )


def completion(day=None):
    day = day or (date.today() + timedelta(days=5)).isoformat()
    return json.dumps({"courses": [
        {"summary": "程序设计", "date": day, "start": "08:00", "end": "09:30"},
    ]}, ensure_ascii=False)


def make_pdf(pages):
    """Write a tiny PDF text fixture without relying on the conflicting core pypdf."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for i, text in enumerate(pages):
        page_id = 3 + i * 2
        stream_id = page_id + 1
        content = b"" if text is None else (
            b"BT /F1 12 Tf 20 350 Td (" + text.encode("ascii") + b") Tj ET"
        )
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> "
            f"/Contents {stream_id} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        data += f"{offset:010d} 00000 n \n".encode()
    data += f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(data)


class Files(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)

    def test_text_formats_and_preserve_layout(self):
        from docx import Document
        from openpyxl import Workbook

        p = self.root / "schedule.json"
        p.write_text('{"name":"程序设计"}', encoding="utf-8")
        self.assertIn("程序设计", importer.extract(p)[1])

        p = self.root / "schedule.html"
        p.write_text("<head>ignore</head><script>ignore</script><table><tr><td>Mon</td>"
                     "<td></td><td>Wed</td></tr><tr><td>Math</td></tr></table>", encoding="utf-8")
        text = importer.extract(p)[1]
        self.assertNotIn("ignore", text)
        self.assertIn("Mon |  | Wed", text)
        self.assertIn("\n | Math", text)

        p = self.root / "schedule.xlsx"
        book = Workbook()
        book.active.append(["Mon", None, "Wed"])
        book.active.append(["Math", None, "Physics"])
        book.save(p)
        self.assertIn("Mon |  | Wed\nMath |  | Physics", importer.extract(p)[1])

        p = self.root / "schedule.docx"
        document = Document()
        document.add_paragraph("Term 2026")
        table = document.add_table(rows=1, cols=2)
        table.rows[0].cells[1].text = "Math"
        document.save(p)
        self.assertIn("Term 2026\n | Math", importer.extract(p)[1])

        p = self.root / "schedule.pdf"
        p.write_bytes(make_pdf(["Math Mon 08:00"]))
        self.assertIn("Math Mon 08:00", importer.extract(p)[1])

    def test_pdf_blank_malformed_page_limit_and_text_limit(self):
        p = self.root / "schedule.pdf"
        p.write_bytes(make_pdf([None]))
        with self.assertRaisesRegex(importer.ImportProblem, "未找到可读取"):
            importer.extract(p)
        p.write_bytes(b"not a pdf")
        with self.assertRaises(importer.ImportProblem):
            importer.extract(p)
        p.write_bytes(make_pdf([None] * 51))
        with self.assertRaisesRegex(importer.ImportProblem, "页数过多"):
            importer.extract(p)
        p.write_bytes(make_pdf(["Math", "ABCD"]))
        with patch.object(importer, "MAX_TEXT", 9), self.assertRaisesRegex(importer.ImportProblem, "文字过多"):
            importer.extract(p)

    def test_image_type_size_and_unsupported(self):
        from PIL import Image
        p = self.root / "generic.tmp"
        Image.new("RGB", (12, 12)).save(p, format="PNG")
        self.assertEqual(importer.extract(p, ".jpg"), ("image", str(p)))
        with self.assertRaises(importer.ImportProblem):
            importer.extract(p, ".doc")
        with patch.object(importer, "MAX_BYTES", 1):
            with self.assertRaises(importer.ImportProblem):
                importer.extract(p, ".png")

    def test_schema_and_ics_roundtrip(self):
        result = completion()
        valid = importer.parse_model_result(result, plugin.SHANGHAI_TZ)
        importer.validate_ics(valid)
        meeting, = Calendar.from_ical(valid).walk("VEVENT")
        self.assertEqual(str(meeting["summary"]), "程序设计")
        self.assertEqual(meeting.decoded("dtstart").astimezone(plugin.SHANGHAI_TZ).hour, 8)
        for invalid in ("not JSON", '{"courses":[]}', completion("2026-02-30"),
                        result.replace("09:30", "07:30"), result.replace('"08:00"', "8")):
            with self.subTest(invalid=invalid), self.assertRaises(importer.ImportProblem):
                importer.parse_model_result(invalid, plugin.SHANGHAI_TZ)
        with self.assertRaises(importer.ImportProblem):
            importer.validate_ics(b"not ICS")


class PluginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        storage_module.StarTools.get_data_dir = lambda name: self.root
        self.context = SimpleNamespace(
            get_current_chat_provider_id=AsyncMock(return_value="provider"),
            get_all_providers=Mock(return_value=[]),
            llm_generate=AsyncMock(return_value=SimpleNamespace(completion_text=completion())),
            send_message=AsyncMock(return_value=True),
        )
        self.bot = plugin.CoursePlugin(self.context)
        self.storage = self.bot._storage

    def store(self, origin=BIND, owner="001"):
        return self.storage.store_calendar(
            user_id=owner, origin=origin, nickname="同学",
            content=importer.parse_model_result(completion(), plugin.SHANGHAI_TZ),
        )

    async def test_ics_bypasses_ai_and_document_uses_provider(self):
        p = self.root / "adapter.tmp"
        p.write_bytes(importer.parse_model_result(completion(), plugin.SHANGHAI_TZ))
        _, is_ai = await self.bot._extract_schedule(event(), ComponentFile("course.ics", p), "")
        self.assertFalse(is_ai)
        self.context.llm_generate.assert_not_awaited()
        p.write_text('{"semester":"fall"}', encoding="utf-8")
        result, is_ai = await self.bot._extract_schedule(event(), ComponentFile("course.json", p), "第一周周一 2026-09-07")
        self.assertTrue(is_ai)
        importer.validate_ics(result)
        self.context.get_current_chat_provider_id.assert_awaited_once_with(BIND)
        request = self.context.llm_generate.await_args.kwargs
        self.assertIn("第一周周一", request["prompt"])
        self.assertNotIn("tools", request)
        self.assertIsNone(request["image_urls"])

    async def test_downloaded_file_cleanup_respects_original_files(self):
        p = self.root / "fileseg_course_123.json"
        p.write_text("{}", encoding="utf-8")
        attachment = ComponentFile("course.json", p)
        attachment.file_ = str(p)
        with patch.object(plugin, "get_astrbot_temp_path", return_value=self.root):
            await self.bot._extract_schedule(event(), attachment, "")
            self.assertTrue(p.exists())

            async def download():
                attachment.file_ = str(p)
                return str(p)

            attachment.file_ = None
            attachment.get_file = download
            await self.bot._extract_schedule(event(), attachment, "")
            self.assertFalse(p.exists())

    async def test_vision_uses_local_image(self):
        from PIL import Image
        p = self.root / "schedule.png"
        Image.new("RGB", (24, 24)).save(p)
        result, is_ai = await self.bot._extract_schedule(event(), ComponentImage(p), "")
        self.assertTrue(is_ai)
        importer.validate_ics(result)
        self.assertEqual(self.context.llm_generate.await_args.kwargs["image_urls"], [str(p)])

    async def test_selected_import_provider_is_used(self):
        p = self.root / "course.json"
        p.write_text('{"course":"Math"}', encoding="utf-8")
        self.bot._plugin_config = {"import_provider_id": "chosen"}
        self.context.get_all_providers.return_value = [SimpleNamespace(meta=lambda: SimpleNamespace(id="chosen"))]
        await self.bot._extract_schedule(event(), ComponentFile("course.json", p), "")
        self.assertEqual(self.context.llm_generate.await_args.kwargs["chat_provider_id"], "chosen")
        self.context.get_current_chat_provider_id.assert_not_awaited()

    async def test_missing_selected_provider_fails_without_fallback(self):
        p = self.root / "course.json"
        p.write_text('{"course":"Math"}', encoding="utf-8")
        self.bot._plugin_config = {"import_provider_id": "missing"}
        with self.assertRaisesRegex(importer.ImportProblem, "当前不可用"):
            await self.bot._extract_schedule(event(), ComponentFile("course.json", p), "")
        self.context.llm_generate.assert_not_awaited()
        self.context.get_current_chat_provider_id.assert_not_awaited()

    def test_provider_schema_is_native_select(self):
        schema = json.loads((Path(plugin.__file__).parent / "_conf_schema.json").read_text(encoding="utf-8"))
        setting = schema["import_provider_id"]
        self.assertEqual((setting["type"], setting["default"], setting["_special"]),
                         ("string", "", "select_provider"))

    async def test_binding_waiter_only_imports_the_owners_attachment(self):
        p = self.root / "adapter.json"
        p.write_text('{"course":"Math"}', encoding="utf-8")
        send = AsyncMock()
        attachment = ComponentFile("course.json", p)

        def message(origin, owner, text="", parts=()):
            return SimpleNamespace(
                **vars(event(origin, owner)), message_str=text, get_messages=lambda: parts,
                plain_result=str, send=send, get_sender_name=lambda: "同学", stop_event=Mock(),
            )

        starter = message(BIND, "001")
        fake_controller = SimpleNamespace(
            future=SimpleNamespace(done=lambda: False), stop=Mock(), keep=Mock(),
        )

        def session_waiter(**kwargs):
            def decorate(callback):
                async def run(_, session_filter):
                    self.assertNotEqual(session_filter.filter(starter), session_filter.filter(event(OTHER)))
                    await callback(fake_controller, message(BIND, "002", parts=[attachment]))
                    await callback(fake_controller, message(OTHER, "001", parts=[attachment]))
                    attachment.get_file.assert_not_awaited()
                    await callback(fake_controller, message(BIND, "001", "课表补充 第一周周一 2026-09-07"))
                    await callback(fake_controller, message(BIND, "001", parts=[attachment]))
                return run
            return decorate

        with patch.object(plugin, "session_waiter", session_waiter):
            replies = [item async for item in self.bot.bind(starter)]
        self.assertIn("120 秒", replies[0])
        attachment.get_file.assert_awaited_once()
        fake_controller.stop.assert_called_once()
        self.assertEqual(self.storage.get_binding("001").unified_msg_origin, BIND)
        self.assertIn("第一周周一", self.context.llm_generate.await_args.kwargs["prompt"])

    async def test_image_cleanup_requires_a_downloaded_temp_source(self):
        from PIL import Image
        p = self.root / "io_temp_img_test.png"
        Image.new("RGB", (24, 24)).save(p)
        image = ComponentImage(p)
        with patch.object(plugin, "get_astrbot_temp_path", return_value=self.root):
            await self.bot._extract_schedule(event(), image, "")
            self.assertTrue(p.exists())
            image.url = "https://example.invalid/schedule.png"
            await self.bot._extract_schedule(event(), image, "")
            self.assertFalse(p.exists())

    def test_saved_periods_retime_only_trusted_indexed_events_without_altering_ics(self):
        row = json.loads(completion())["courses"][0]
        row.update(first_period=1, last_period=2)
        unindexed = dict(row, summary="按钟点", start="07:00", end="07:30")
        del unindexed["first_period"], unindexed["last_period"]
        calendar = importer.parse_model_result(
            json.dumps({"courses": [row, unindexed]}), plugin.SHANGHAI_TZ
        )
        binding = self.storage.store_calendar(
            user_id="001", origin=BIND, nickname="同学", content=calendar,
            period_times_trusted=True,
        )
        p = self.root / binding.ics_file

        def hours(current_binding):
            return {course.summary: (course.start_time.strftime("%H:%M"), course.end_time.strftime("%H:%M"))
                    for course in self.bot._events_for_binding(current_binding)}

        self.assertEqual(hours(binding)["程序设计"], ("08:00", "09:30"))  # warm base cache
        new_table = {1: ("10:15", "11:00"), 2: ("11:05", "11:50")}
        self.storage.save_period_times(OTHER, "001", new_table)
        self.assertEqual(self.storage.get_period_times(BIND, "001"), new_table)
        self.assertEqual(self.storage.get_period_times(FOREIGN, "001"), {})
        self.assertEqual(self.storage.get_period_times(BIND, "002"), {})
        self.assertTrue(self.storage.get_binding("001").period_times_trusted)
        self.assertEqual(hours(binding), {"按钟点": ("07:00", "07:30"), "程序设计": ("10:15", "11:50")})
        self.assertEqual(p.read_bytes(), calendar)
        self.storage.save_period_times(OTHER, "001", {1: new_table[1]})
        self.assertEqual(hours(binding)["程序设计"], ("08:00", "09:30"))
        self.storage.save_period_times(OTHER, "001", new_table)
        untrusted = self.storage.store_calendar(user_id="001", origin=OTHER, nickname="同学", content=calendar)
        self.assertFalse(untrusted.period_times_trusted)
        self.assertEqual(hours(untrusted)["程序设计"], ("08:00", "09:30"))

    def test_model_periods_only_require_a_complete_range(self):
        row = json.loads(completion())["courses"][0]
        del row["start"], row["end"]
        row.update(first_period=1, last_period=3)
        data = json.dumps({"courses": [row]})
        table = {1: ("08:25", "09:10"), 2: ("09:15", "10:00"), 3: ("10:15", "11:00")}
        calendar = importer.parse_model_result(data, plugin.SHANGHAI_TZ, table)
        meeting, = Calendar.from_ical(calendar).walk("VEVENT")
        self.assertEqual(meeting.decoded("dtstart").astimezone(plugin.SHANGHAI_TZ).strftime("%H:%M"), "08:25")
        self.assertEqual(meeting.decoded("dtend").astimezone(plugin.SHANGHAI_TZ).strftime("%H:%M"), "11:00")
        with self.assertRaises(importer.ImportProblem):
            importer.parse_model_result(data, plugin.SHANGHAI_TZ, {1: table[1], 3: table[3]})

    async def test_period_command_formats_and_owner_guard(self):
        from PIL import Image

        expected = {1: ("08:25", "09:10"), 2: ("09:15", "10:00")}
        as_json = json.dumps({"1": ["08:25", "09:10"], "2": ["09:15", "10:00"]})
        file_path = self.root / "adapter.tmp"
        file_path.write_text(as_json, encoding="utf-8")
        img_path = self.root / "schedule.png"
        Image.new("RGB", (20, 20)).save(img_path)

        def message(origin=BIND, owner="001", text="", parts=()):
            return SimpleNamespace(
                **vars(event(origin, owner)), message_str=text, get_messages=lambda: parts,
                plain_result=str, send=AsyncMock(), stop_event=Mock(),
            )

        for text, parts in [
            ("第1节 08:25-09:10\n第2节 09:15-10:00", []),
            (as_json, []),
            ("", [ComponentFile("course.json", file_path)]),
            ("", [ComponentImage(img_path)]),
        ]:
            with self.subTest(parts=parts, text=text):
                old = {1: ("07:00", "07:45")}
                self.storage.save_period_times(BIND, "001", old)
                self.context.llm_generate.reset_mock()
                self.context.llm_generate.return_value = SimpleNamespace(completion_text=as_json)
                controller = SimpleNamespace(
                    future=SimpleNamespace(done=lambda: False), stop=Mock(), keep=Mock(),
                )

                def session_waiter(**kwargs):
                    def decorate(callback):
                        async def run(_, session_filter):
                            await callback(controller, message(owner="002", text=as_json))
                            await callback(controller, message(origin=OTHER, text=as_json))
                            await callback(controller, message(text="第1节 09:00-08:00"))
                            self.assertEqual(self.storage.get_period_times(BIND, "001"), old)
                            self.assertEqual(self.storage.get_period_times(BIND, "002"), {})
                            await callback(controller, message(text=text, parts=parts))
                        return run
                    return decorate

                with patch.object(plugin, "session_waiter", session_waiter):
                    replies = [value async for value in self.bot.set_period_times(message())]
                self.assertTrue(replies)
                self.assertEqual(self.storage.get_period_times(BIND, "001"), expected)
                self.assertIsNone(self.storage.get_binding("001"))  # Configurable before binding.
                controller.stop.assert_called_once()
                if parts and isinstance(parts[0], ComponentImage):
                    self.assertEqual(self.context.llm_generate.await_args.kwargs["image_urls"], [str(img_path)])
                else:
                    self.context.llm_generate.assert_not_awaited()

    async def test_period_file_keeps_raw_json_validation_and_handles_utf8_text(self):
        path = self.root / "adapter.tmp"
        path.write_bytes(b'{"1":["08:25","09:10"],"1":["09:15","10:00"]}')
        with self.assertRaises(importer.ImportProblem):
            await self.bot._extract_period_times(event(), ComponentFile("period.json", path))

        path.write_bytes(b" " * (plugin.MAX_INPUT + 1))
        with self.assertRaises(importer.ImportProblem):
            await self.bot._extract_period_times(event(), ComponentFile("period.json", path))

        path.write_text("第1节 08:25-09:10", encoding="utf-8-sig")
        self.assertEqual(
            await self.bot._extract_period_times(event(), ComponentFile("period.txt", path)),
            {1: ("08:25", "09:10")},
        )
        self.context.llm_generate.assert_not_awaited()

    def test_period_profile_write_failure_preserves_previous_profile(self):
        original = {1: ("08:25", "09:10")}
        self.storage.save_period_times(BIND, "001", original)
        with patch.object(storage_module.os, "replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                self.storage.save_period_times(BIND, "001", {1: ("09:15", "10:00")})
        self.assertEqual(self.storage.get_period_times(OTHER, "001"), original)
        self.assertFalse(list(self.root.glob("period-times-*.tmp")))

    async def test_retimed_course_drives_reminder(self):
        fixed = datetime.now(plugin.SHANGHAI_TZ).replace(hour=9, minute=55, second=0, microsecond=0)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        row = json.loads(completion(fixed.date().isoformat()))["courses"][0]
        row.update(first_period=1, last_period=1)
        data = importer.parse_model_result(json.dumps({"courses": [row]}), plugin.SHANGHAI_TZ)
        self.storage.store_calendar(
            user_id="001", origin=BIND, nickname="同学", content=data, period_times_trusted=True,
        )
        with patch.object(plugin, "datetime", Clock):
            await self.bot._tick_reminder()
            self.context.send_message.assert_not_awaited()
            self.storage.save_period_times(BIND, "001", {1: ("10:00", "10:45")})
            await self.bot._tick_reminder()
        self.context.send_message.assert_awaited_once()
        self.assertEqual(self.context.send_message.await_args.args[0], BIND)
        self.assertIn("程序设计", self.context.send_message.await_args.args[1].text)

    def test_all_schedule_consumers_use_shared_read_path(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CoursePlugin")
        callers = {
            node.name for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(child, ast.Attribute) and child.attr == "_events_for_binding"
                    for child in ast.walk(node))
        }
        self.assertEqual(callers, {"week", "next_week", "_send_day_schedule", "_daily_push_handler", "_tick_reminder"})

    def test_cross_group_identity_defaults_and_persisted_opt_out(self):
        old = self.store()
        self.assertEqual(plugin._reminder_targets(old), (BIND,))
        self.assertEqual(self.bot._owned_binding(event(OTHER)).ics_file, old.ics_file)
        self.assertIsNone(self.bot._owned_binding(event(owner="other-person")))
        self.assertIsNone(self.bot._owned_binding(event(FOREIGN)))
        self.bot._set_group_reminders(event(PRIVATE), True)
        self.bot._set_group_reminders(event(FOREIGN), True)
        self.bot._set_group_reminders(event(OTHER, "other-person"), True)
        self.assertEqual(plugin._reminder_targets(self.storage.get_binding("001")), (BIND,))
        self.bot._set_group_reminders(event(OTHER), True)
        self.bot._set_group_reminders(event(BIND), False)
        new = self.store(OTHER)
        self.assertEqual(new.unified_msg_origin, BIND)
        self.assertEqual(plugin._reminder_targets(new), (OTHER,))
        self.assertFalse((self.root / old.ics_file).exists())
        self.assertTrue((self.root / new.ics_file).is_file())

    def test_disk_failure_keeps_previous_binding_and_calendar(self):
        old = self.store()
        with patch.object(self.storage, "save_bindings", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                self.store(OTHER)
        self.assertEqual(self.storage.get_binding(old.user_id).ics_file, old.ics_file)
        self.assertEqual(list((self.root / "ics").glob("*.ics")), [self.root / old.ics_file])

    def test_legacy_private_binding_and_sender_guard(self):
        binding = self.store(PRIVATE)
        saved = self.root / "bindings.json"
        payload = json.loads(saved.read_text(encoding="utf-8"))
        del payload["bindings"][binding.user_id]["group_reminders"]
        saved.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(plugin._reminder_targets(self.storage.get_binding(binding.user_id)), (PRIVATE,))
        session_filter = plugin._OwnerSessionFilter("import")
        self.assertNotEqual(session_filter.filter(event()), session_filter.filter(event(owner="other")))
        self.assertNotEqual(session_filter.filter(event()), session_filter.filter(event(OTHER)))

    async def test_reminder_failure_retries_only_failed_group(self):
        self.store()
        self.bot._set_group_reminders(event(OTHER), True)
        now = datetime.now(plugin.SHANGHAI_TZ)
        meeting = plugin.CourseEvent("Math", now + timedelta(minutes=10), now + timedelta(hours=1))
        self.bot._parser.parse_ics_file = Mock(return_value=[meeting])
        self.context.send_message.side_effect = [False, True, True]
        await self.bot._tick_reminder()
        await self.bot._tick_reminder()
        await self.bot._tick_reminder()
        self.assertEqual([call.args[0] for call in self.context.send_message.await_args_list], [BIND, OTHER, BIND])


if __name__ == "__main__":
    unittest.main()
