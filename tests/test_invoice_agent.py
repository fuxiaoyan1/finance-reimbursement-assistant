import unittest
from decimal import Decimal
from pathlib import Path
import tempfile

from invoice_agent import (
    InvoiceRecord,
    apply_llm_result,
    build_agent_config,
    collect_invoice_files,
    invoice_file_id,
    mark_duplicate_invoices,
    parse_invoice_text,
    rmb_upper,
)


class InvoiceParserTests(unittest.TestCase):
    def test_parse_chinese_invoice_fields(self):
        text = """
        发票代码: 044032300111
        发票号码: 12345678
        开票日期: 2026年05月20日
        购买方名称: 某某科技有限公司
        销售方名称: 示例餐饮有限公司
        合计 ¥100.00 税额 ¥6.00
        价税合计(小写) ¥106.00
        """
        parsed = parse_invoice_text(text)
        self.assertEqual(parsed["invoice_code"], "044032300111")
        self.assertEqual(parsed["invoice_number"], "12345678")
        self.assertEqual(parsed["invoice_date"], "2026-05-20")
        self.assertEqual(parsed["total_amount"], Decimal("106.00"))
        self.assertEqual(parsed["tax_amount"], Decimal("6.00"))

    def test_parse_english_invoice_fields(self):
        text = """
        Invoice No: INV-2026-0001
        Date: 2026-05-21
        Seller Name: Example Vendor LLC
        Buyer Name: Example Buyer LLC
        Total: $88.50
        """
        parsed = parse_invoice_text(text)
        self.assertEqual(parsed["invoice_number"], "INV-2026-0001")
        self.assertEqual(parsed["invoice_date"], "2026-05-21")
        self.assertEqual(parsed["total_amount"], Decimal("88.50"))

    def test_rmb_upper(self):
        self.assertEqual(rmb_upper(Decimal("0.00")), "零元整")
        self.assertEqual(rmb_upper(Decimal("106.00")), "壹佰零陆元整")
        self.assertEqual(rmb_upper(Decimal("106.05")), "壹佰零陆元零伍分")

    def test_apply_llm_result_overrides_rule_candidate(self):
        record = InvoiceRecord(index=1, custom_serial="FP-001", source_path=__file__, file_type="PDF")
        notes = []
        apply_llm_result(
            record,
            {
                "invoice_code": "",
                "invoice_number": "26117000000649874912",
                "invoice_date": "2026年05月04日",
                "seller": "示例出行科技有限公司",
                "buyer": "示例采购方有限公司",
                "amount": 109.47,
                "tax_amount": 3.28,
                "total_amount": 112.75,
                "category": "交通费",
                "include_in_total": True,
                "confidence": 0.93,
                "review_notes": ["字段来自LLM校正"],
            },
            notes,
        )
        self.assertEqual(record.invoice_number, "26117000000649874912")
        self.assertEqual(record.invoice_date, "2026-05-04")
        self.assertEqual(record.total_amount, Decimal("112.75"))
        self.assertEqual(record.category, "交通费")
        self.assertEqual(record.llm_confidence, 0.93)
        self.assertIn("字段来自LLM校正", notes)

    def test_duplicate_invoice_is_excluded_from_total(self):
        first = InvoiceRecord(
            index=1,
            custom_serial="FP-001",
            source_path=__file__,
            file_type="PDF",
            invoice_number="12345678",
            total_amount=Decimal("50.00"),
        )
        second = InvoiceRecord(
            index=2,
            custom_serial="FP-002",
            source_path=__file__,
            file_type="PDF",
            invoice_number="12345678",
            total_amount=Decimal("50.00"),
        )
        mark_duplicate_invoices([first, second])
        self.assertTrue(first.include_in_total)
        self.assertFalse(second.include_in_total)
        self.assertEqual(second.review_status, "重复")

    def test_agent_config_auto_uses_key(self):
        class Args:
            no_llm = False
            use_llm = False
            llm_provider = None
            llm_model = None
            llm_base_url = None
            llm_timeout = None
            llm_all = False

        config = build_agent_config(Args(), {"use_llm": False})
        self.assertFalse(config.use_llm)

    def test_codex_display_model_without_explicit_model(self):
        from invoice_agent import AgentConfig, LlmClient

        client = LlmClient(AgentConfig(use_llm=True, llm_provider="codex", llm_model=""))
        self.assertEqual(client.display_model, "codex")

    def test_collect_invoice_files_skips_reimbursed(self):
        with tempfile.TemporaryDirectory() as tmp:
            invoice = Path(tmp) / "invoice.pdf"
            invoice.write_bytes(b"%PDF-1.4 test")
            ledger = {"reimbursed_files": {invoice_file_id(invoice): {"batch_id": "RB-test"}}}
            self.assertEqual(collect_invoice_files(Path(tmp), ledger=ledger), [])
            self.assertEqual(collect_invoice_files(Path(tmp), ledger=ledger, include_reimbursed=True), [invoice])


if __name__ == "__main__":
    unittest.main()
