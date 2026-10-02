import os
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase


class TestShopAIChat(TransactionCase):
    def test_product_lookup_and_conversation(self):
        product = self.env["product.product"].create({
            "name": "Chat Test Biscuit", "list_price": 28,
        })
        self.env["ir.config_parameter"].sudo().set_param("wholesale_shop_pos.ai_api_key", "test-key")
        chat = self.env["shop.ai.chat"].create({"prompt": "What is the price of Chat Test Biscuit?"})
        calls = []

        def fake_response(_chat, payload, _key):
            calls.append(payload)
            if len(calls) == 1:
                return {"output": [{
                    "type": "function_call", "name": "get_record", "call_id": "call_1",
                    "arguments": '{"model":"product.product","id":%s}' % product.id,
                }]}
            return {"output": [{"type": "message", "content": [
                {"type": "output_text", "text": "Chat Test Biscuit sells for 28."},
            ]}]}

        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch.object(
            type(chat), "_request_openai", fake_response,
        ):
            chat.action_send()

        self.assertEqual(len(chat.message_ids), 2)
        self.assertEqual(chat.message_ids.mapped("role"), ["user", "assistant"])
        self.assertIn("28", chat.message_ids[-1].content)
        self.assertEqual(calls[1]["input"][-1]["type"], "function_call_output")
        self.assertIn("Chat Test Biscuit", calls[1]["input"][-1]["output"])
        self.assertFalse(chat.prompt)

    def test_other_user_cannot_read_or_send_chat(self):
        chat = self.env["shop.ai.chat"].create({"prompt": "Private question"})
        other = self.env["res.users"].create({
            "name": "Other Chat User", "login": "other_chat_user",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self.assertFalse(self.env["shop.ai.chat"].with_user(other).search([
            ("id", "=", chat.id),
        ]))
        with self.assertRaises(AccessError):
            chat.with_user(other).action_send()
