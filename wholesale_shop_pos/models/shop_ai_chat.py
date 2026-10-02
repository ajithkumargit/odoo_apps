"""A permission-aware Odoo data assistant. No shell or SQL execution."""

import json
import os
from urllib import error, request

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


ALLOWED_MODEL_PREFIXES = (
    "product.", "stock.", "purchase.", "account.", "pos.", "shop.",
)
PRIVATE_FIELD_PARTS = ("password", "secret", "token", "api_key", "credential")


class ShopAIChat(models.Model):
    _name = "shop.ai.chat"
    _description = "Wholesale Shop AI Chat"
    _order = "write_date desc, id desc"

    name = fields.Char(default="New Chat", required=True)
    user_id = fields.Many2one("res.users", required=True, default=lambda self: self.env.user, readonly=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, readonly=True)
    message_ids = fields.One2many("shop.ai.chat.message", "chat_id", string="Conversation", readonly=True)
    prompt = fields.Text(string="Message", copy=False)

    def _check_owner(self):
        for chat in self:
            if chat.user_id != self.env.user:
                raise AccessError(_("You can only use your own chats."))

    def _available_models(self):
        self._check_owner()
        names = self.env["ir.model"].search([
            "|", "|", "|", "|", "|",
            ("model", "=like", "product.%"),
            ("model", "=like", "stock.%"),
            ("model", "=like", "purchase.%"),
            ("model", "=like", "account.%"),
            ("model", "=like", "pos.%"),
            ("model", "=like", "shop.%"),
        ], limit=200)
        return [{"model": item.model, "name": item.name} for item in names
                if item.model in self.env and self.env[item.model].has_access("read")]

    def _model(self, name):
        if not isinstance(name, str) or not name.startswith(ALLOWED_MODEL_PREFIXES) or name not in self.env:
            raise UserError(_("This data model is not available in the chat."))
        model = self.env[name]
        model.check_access("read")
        return model

    def _record_details(self, record):
        names = record.fields_get()
        values = {}
        for name, meta in names.items():
            if (name not in record._fields or any(part in name.lower() for part in PRIVATE_FIELD_PARTS)
                    or meta.get("type") in ("binary", "html")
                    or name.startswith("message_") or name.startswith("activity_")):
                continue
            try:
                value = record[name]
                if record._fields[name].type == "many2one":
                    value = {"id": value.id, "name": value.display_name} if value else None
                elif record._fields[name].type in ("many2many", "one2many"):
                    if not record._name.startswith("product."):
                        continue
                    value = [{"id": item.id, "name": item.display_name}
                             for item in value[:20] if item.has_access("read")]
                elif hasattr(value, "isoformat"):
                    value = value.isoformat()
                elif not isinstance(value, (str, int, float, bool, type(None))):
                    continue
                values[name] = value
            except (AccessError, UserError):
                continue
            if len(json.dumps(values, default=str)) > 18000:
                values.pop(name, None)
                break
        return {"id": record.id, "model": record._name, "fields": values}

    def _run_data_tool(self, name, args):
        self._check_owner()
        if name == "list_models":
            return self._available_models()
        model = self._model(args.get("model"))
        if name == "search_records":
            query = (args.get("query") or "").strip()[:150]
            limit = min(max(int(args.get("limit") or 10), 1), 20)
            if query:
                matches = model.name_search(name=query, limit=limit)
                records = model.browse([item[0] for item in matches]).exists()
            else:
                records = model.search([], order="id desc", limit=limit)
            return [{"id": record.id, "name": record.display_name} for record in records]
        if name == "get_record":
            record = model.browse(int(args.get("id") or 0)).exists()
            if not record:
                return {"error": "Record not found or inaccessible."}
            record.check_access("read")
            return self._record_details(record)
        return {"error": "Unknown data tool."}

    @staticmethod
    def _tool_definitions():
        def tool(name, description, properties, required):
            return {"type": "function", "name": name, "description": description,
                    "strict": True, "parameters": {"type": "object", "properties": properties,
                    "required": required, "additionalProperties": False}}
        return [
            tool("list_models", "List Odoo business models readable by the current user.", {}, []),
            tool("search_records", "Find business records by name, reference or barcode in an Odoo model.",
                 {"model": {"type": "string"}, "query": {"type": "string"},
                  "limit": {"type": "integer"}}, ["model", "query", "limit"]),
            tool("get_record", "Read all supported details of one business record by its model and ID.",
                 {"model": {"type": "string"}, "id": {"type": "integer"}}, ["model", "id"]),
        ]

    def _request_openai(self, payload, key):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = request.Request(
            "https://api.openai.com/v1/responses", data=data,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=45) as response:
                return json.load(response)
        except error.HTTPError as exc:
            raise UserError(_("The AI service returned HTTP %(status)s. Check the API key and model setting.", status=exc.code)) from exc
        except (error.URLError, TimeoutError) as exc:
            raise UserError(_("The AI service could not be reached. Check server internet access.")) from exc

    def action_send(self):
        self.ensure_one()
        self._check_owner()
        question = (self.prompt or "").strip()
        if not question:
            raise UserError(_("Enter a message first."))
        if len(question) > 4000:
            raise UserError(_("Keep the message under 4,000 characters."))
        parameters = self.env["ir.config_parameter"].sudo()
        key = (os.environ.get("OPENAI_API_KEY") or parameters.get_param(
            "wholesale_shop_pos.ai_api_key", "")).strip()
        if not key:
            raise UserError(_("Set the OpenAI API key in Wholesale Shop Settings first."))
        model = parameters.get_param("wholesale_shop_pos.ai_model", "gpt-4.1-mini")
        history = self.message_ids.sorted("id")[-12:]
        inputs = [{"role": "user" if item.role == "user" else "assistant", "content": item.content}
                  for item in history]
        inputs.append({"role": "user", "content": question})
        instructions = (
            "You are the Wholesale Shop Odoo assistant. Answer from Odoo data tools, never invent "
            "database values. The tools read records only with the current user's access rights. "
            "For code edits, deployment or restarts, explain that this chat cannot execute those actions "
            "and give a concrete request for an administrator or development agent. "
            "Never claim a server change has happened. Keep answers concise."
        )
        answer = ""
        for _step in range(4):
            result = self._request_openai({
                "model": model, "instructions": instructions, "input": inputs,
                "tools": self._tool_definitions(), "store": False,
                "parallel_tool_calls": False,
            }, key)
            output = result.get("output") or []
            calls = [item for item in output if item.get("type") == "function_call"]
            if not calls:
                answer = "\n".join(part.get("text", "") for item in output
                                   if item.get("type") == "message"
                                   for part in item.get("content", []) if part.get("type") == "output_text").strip()
                break
            inputs.extend(output)
            for call in calls:
                try:
                    tool_result = self._run_data_tool(call["name"], json.loads(call.get("arguments") or "{}"))
                except (AccessError, UserError, ValueError, TypeError) as exc:
                    tool_result = {"error": str(exc)}
                inputs.append({"type": "function_call_output", "call_id": call["call_id"],
                               "output": json.dumps(tool_result, ensure_ascii=False, default=str)[:24000]})
        if not answer:
            answer = _("I could not finish that request. Please ask for a specific record or product.")
        Message = self.env["shop.ai.chat.message"]
        Message.create({"chat_id": self.id, "role": "user", "content": question})
        Message.create({"chat_id": self.id, "role": "assistant", "content": answer})
        self.prompt = False
        if self.name == "New Chat":
            self.name = question[:70]
        return {"type": "ir.actions.client", "tag": "soft_reload"}


class ShopAIChatMessage(models.Model):
    _name = "shop.ai.chat.message"
    _description = "Wholesale Shop AI Chat Message"
    _order = "id"

    chat_id = fields.Many2one("shop.ai.chat", required=True, ondelete="cascade", index=True)
    role = fields.Selection([("user", "You"), ("assistant", "Assistant")], required=True)
    content = fields.Text(required=True)


class ShopAIChatSettings(models.TransientModel):
    _inherit = "res.config.settings"

    shop_ai_api_key = fields.Char(
        string="OpenAI API Key", config_parameter="wholesale_shop_pos.ai_api_key",
        groups="base.group_system",
    )
    shop_ai_model = fields.Char(
        string="OpenAI Model", config_parameter="wholesale_shop_pos.ai_model",
        default="gpt-4.1-mini", groups="base.group_system",
    )
