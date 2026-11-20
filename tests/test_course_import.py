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


class Files(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)

    def test_text_formats_and_preserve_layout(self):
        from docx import Document
        from openpyxl import Workbook
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

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
        writer = PdfWriter()
        page = writer.add_blank_page(400, 400)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 200 Td (Math Mon 08:00) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        with p.open("wb") as binary:
            writer.write(binary)
        self.assertIn("Math Mon 08:00", importer.extract(p)[1])

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
        await self.bot._extract_schedule(event(), ComponentFile("course.ics", p), "")
        self.context.llm_generate.assert_not_awaited()
        p.write_text('{"semester":"fall"}', encoding="utf-8")
        result = await self.bot._extract_schedule(event(), ComponentFile("course.json", p), "第一周周一 2026-09-07")
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
        result = await self.bot._extract_schedule(event(), ComponentImage(p), "")
        importer.validate_ics(result)
        self.assertEqual(self.context.llm_generate.await_args.kwargs["image_urls"], [str(p)])

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
