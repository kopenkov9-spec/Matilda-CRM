from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Kind = Literal['companies', 'contacts', 'deals', 'tasks', 'meetings', 'notes']
KINDS = ('companies', 'contacts', 'deals', 'tasks', 'meetings', 'notes')
LABELS = dict(zip(KINDS, ('Фирми', 'Контакти', 'Сделки', 'Задачи', 'Срещи', 'Бележки')))
STATUSES = {
    'companies': ('active', 'archived'), 'contacts': ('active', 'archived'),
    'deals': ('new', 'proposal', 'negotiation', 'won', 'lost', 'archived'),
    'tasks': ('open', 'in_progress', 'done', 'cancelled', 'archived'),
    'meetings': ('scheduled', 'done', 'cancelled', 'archived'),
    'notes': ('active', 'archived'),
}

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Record(Strict):
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(default='', max_length=12000)
    company_id: str | None = None
    contact_id: str | None = None
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=60)
    start_at: str | None = None
    end_at: str | None = None
    due_at: str | None = None
    status: str | None = None
    amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')

    @field_validator('title')
    @classmethod
    def clean_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Заглавието е задължително.')
        return value

    @field_validator('start_at', 'end_at', 'due_at')
    @classmethod
    def date_with_timezone(cls, value):
        if value is None:
            return value
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            raise ValueError('Датата трябва да има часова зона.')
        return dt.isoformat()

    @field_validator('email')
    @classmethod
    def check_email(cls, value):
        if value is not None and ('@' not in value or any(c.isspace() for c in value)):
            raise ValueError('Невалиден имейл.')
        return value

    @model_validator(mode='after')
    def check_order(self):
        if self.start_at and self.end_at and datetime.fromisoformat(self.end_at) <= datetime.fromisoformat(self.start_at):
            raise ValueError('Краят трябва да е след началото.')
        return self

class Operation(Strict):
    entity: Kind
    action: Literal['create', 'update'] = 'create'
    target_id: str | None = None
    expected_version: int | None = None
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(default='', max_length=6000)
    company_name: str | None = None
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    due_at: str | None = None
    status: str | None = None
    amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = None
    evidence: str = Field(min_length=1, max_length=2000)

    @field_validator('evidence')
    @classmethod
    def nonblank_evidence(cls, value):
        if not value.strip():
            raise ValueError('Липсва точен откъс от източника.')
        return value.strip()

class Query(Strict):
    entity: Kind | None = None
    search: str = Field(default='', max_length=200)
    date_from: str | None = None
    date_to: str | None = None
    status: str | None = None

    @field_validator('date_from', 'date_to')
    @classmethod
    def check_date(cls, value):
        if value is not None:
            from datetime import date
            date.fromisoformat(value)
        return value

class Plan(Strict):
    intent: Literal['write', 'query', 'clarify']
    operations: list[Operation] = Field(default_factory=list, max_length=12)
    query: Query | None = None
    question: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def validate_intent(self):
        if self.intent == 'write' and not self.operations:
            raise ValueError('Липсват предложени записи.')
        if self.intent != 'write' and self.operations:
            raise ValueError('Запитването не може да променя данни.')
        if self.intent == 'query' and self.query is None:
            raise ValueError('Липсва справка.')
        if self.intent == 'clarify' and not self.question.strip():
            raise ValueError('Липсва уточняващ въпрос.')
        return self
