"""AI clients and persistent spending safeguards; no invoice decisions here."""
import fcntl
import hashlib
import json
import os
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import ssl
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "contract_agent_output"



MODEL = "google/gemini-3.5-flash-lite"


def load_keys():
    values = {k.lower(): v for k, v in os.environ.items()}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.removeprefix("export ").split("=", 1)
                values.setdefault(k.strip().lower(), v.strip().strip("\"'"))
    result = {k: values.get(k) for k in ("voyage_api_key", "openrouter_api_key")}
    if not result["openrouter_api_key"]:
        raise ValueError("Configure openrouter_api_key in the local .env")
    return result


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class API:
    """Original contract-extraction client; its budget is separate from invoice replay."""
    def __init__(self, keys, budget=2.0, output_dir=None, cache_only=False):
        self.keys, self.budget = keys, budget
        self.cache_only = cache_only
        self.lock = threading.Lock()
        output_dir = Path(output_dir) if output_dir is not None else OUT
        self.cache = output_dir / "api_cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.ledger_path = output_dir / "usage.json"
        self.ledger = json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else []
        self.context = ssl.create_default_context(cafile="/etc/ssl/cert.pem" if Path("/etc/ssl/cert.pem").exists() else None)

    def call(self, kind, body):
        encoded = json.dumps(body, sort_keys=True).encode()
        request_id = hashlib.sha256(kind.encode() + encoded).hexdigest()
        cache = self.cache / (request_id + ".json")
        if cache.exists():
            return json.loads(cache.read_text())
        if self.cache_only:
            raise RuntimeError("Offline cache miss; network calls are disabled")
        with self.lock:
            if sum(x.get("cost_usd", 0) or 0 for x in self.ledger) >= self.budget - 0.10:
                raise RuntimeError("Pilot spending guard reached (reserves room for in-flight requests)")
        endpoints = {"embedding": "https://api.voyageai.com/v1/embeddings", "rerank": "https://api.voyageai.com/v1/rerank", "reasoning": "https://openrouter.ai/api/v1/chat/completions"}
        key = self.keys["openrouter_api_key" if kind == "reasoning" else "voyage_api_key"]
        request = urllib.request.Request(endpoints[kind], data=encoded, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, context=self.context, timeout=50) as response:
                    result = json.load(response)
                break
            except urllib.error.HTTPError as error:
                # Do not print headers, keys, request bodies or raw provider errors.
                if error.code == 429 and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise RuntimeError(f"{kind} HTTP {error.code}; no credentials logged") from None
            except (urllib.error.URLError, TimeoutError):
                raise RuntimeError(f"{kind} network error; not automatically retried because billing is uncertain") from None
        usage = result.get("usage", {})
        if kind == "reasoning":
            cost = usage.get("cost")
            cost_type = "provider_reported" if cost is not None else "estimated_from_usage"
            if cost is None:
                cost = usage.get("prompt_tokens", 0) * .30 / 1e6 + usage.get("completion_tokens", 0) * 2.50 / 1e6
        else:
            cost = usage.get("total_tokens", 0) * (.12 if kind == "embedding" else .05) / 1e6
            cost_type = "list_price_estimate_before_free_credits"
        with self.lock:
            self.ledger.append({"request_id": request_id, "kind": kind, "model": body["model"],
                                "response_id": result.get("id"), "usage": usage, "cost_usd": cost, "cost_type": cost_type})
            save(self.ledger_path, self.ledger)
        save(cache, result)
        if "error" in result:
            raise RuntimeError(f"{kind} returned an API error; metering preserved")
        return result


# Invoice fallback: shared, durable USD 2 ledger.


class BudgetStop(RuntimeError):
    pass


class BudgetedAI:
    def __init__(self, directory, key=None, budget=2, offline=False, transport=None):
        amount = Decimal(str(budget))
        if not amount.is_finite() or not 0 < amount <= 2:
            raise ValueError('Budget must be positive and at most USD 2')
        self.limit = int(amount * 1_000_000)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cache = self.directory / 'api_cache'
        self.cache.mkdir(exist_ok=True)
        self.lock = (self.directory / 'spending.lock').open('a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.path = self.directory / 'spending.json'
        self.ledger = json.loads(self.path.read_text()) if self.path.exists() else []
        self.key, self.offline = key, offline
        self.transport = transport or self._send

    def close(self):
        self.lock.close()

    def persist(self):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.ledger, indent=2) + '\n')
        temp.replace(self.path)

    @property
    def committed(self):
        return sum(r['charged_or_reserved_micro_usd'] for r in self.ledger)

    def summary(self):
        return {'budget_usd': self.limit / 1e6, 'calls_attempted': len(self.ledger),
                'provider_reported_cost_usd': sum(r.get('provider_cost_usd', 0) for r in self.ledger),
                'charged_or_reserved_usd': self.committed / 1e6,
                'uncertain_requests': sum(r['status'] != 'metered' for r in self.ledger),
                'note': 'Local run guard; uncertain charges stay reserved. Not an account-wide provider cap.'}

    def _send(self, encoded):
        request = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',
            data=encoded, headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        context = ssl.create_default_context(cafile='/etc/ssl/cert.pem')
        with urllib.request.urlopen(request, context=context, timeout=50) as response:
            return json.load(response)

    def call(self, body):
        if any(r['charged_or_reserved_micro_usd'] > r['reserved_micro_usd'] for r in self.ledger):
            raise BudgetStop('Previous provider charge exceeded reservation; all new calls disabled')
        # Only the reviewed text model and bounded completion size are allowed.
        if body.get('model') != MODEL or type(body.get('max_tokens')) is not int or not 1 <= body['max_tokens'] <= 4000:
            raise ValueError('Unsupported model or completion budget')
        if set(body) - {'model', 'messages', 'max_tokens', 'temperature', 'reasoning', 'response_format'}:
            raise ValueError('Unexpected request feature; no tools, media, search or fallbacks allowed')
        if any(not isinstance(m.get('content'), str) for m in body['messages']):
            raise ValueError('Text only')
        body = {**body, 'provider': {'max_price': {'prompt': .30, 'completion': 2.50, 'request': 0},
                                    'allow_fallbacks': False, 'require_parameters': True}}
        encoded = json.dumps(body, sort_keys=True).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        cached = self.cache / (digest + '.json')
        if cached.exists():
            return json.loads(cached.read_text())
        if self.offline:
            raise BudgetStop('Offline cache miss')
        if any(r['request_id'] == digest for r in self.ledger):
            raise BudgetStop('Previously attempted request has no cache; automatic retry prohibited')
        # Conservative byte-based token allowance plus framing/schema margin,
        # then 2x price margin and $0.005 overhead. All calls are serialized.
        allowance = (2 * len(encoded) + 8192) * Decimal('.30') + body['max_tokens'] * Decimal('2.50')
        reserve = int((allowance * 2 + 5000).to_integral_value(rounding=ROUND_CEILING))
        if self.committed + reserve > self.limit:
            raise BudgetStop('USD 2 safeguard: insufficient budget for full request reservation')
        entry = {'request_id': digest, 'status': 'pending_or_uncertain',
                 'reserved_micro_usd': reserve, 'charged_or_reserved_micro_usd': reserve}
        self.ledger.append(entry)
        self.persist()  # Durable BEFORE any network side effect.
        try:
            result = self.transport(encoded)
        except Exception:
            raise RuntimeError('API failed; reservation retained, no retry and no credentials logged') from None
        usage = result.get('usage', {})
        cost = usage.get('cost')
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and Decimal(str(cost)).is_finite() and cost >= 0:
            charged = int((Decimal(str(cost)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
            entry.update(status='metered', charged_or_reserved_micro_usd=charged,
                         provider_cost_usd=cost, usage=usage, response_id=result.get('id'))
        self.persist()
        cached.write_text(json.dumps(result))
        if entry['charged_or_reserved_micro_usd'] > reserve:
            # Unexpected billing violates the reservation model: stop all new work.
            raise BudgetStop('Provider cost exceeded reservation; investigate before further calls')
        return result
