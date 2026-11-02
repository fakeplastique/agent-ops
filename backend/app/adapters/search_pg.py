import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import tenant_session
from app.ports.search import MAX_WINDOW, SearchHit, SearchPage

CANDIDATES = MAX_WINDOW  # per source and in total; tests shrink it
# websearch_to_tsquery never raises on operators or stray punctuation (unlike to_tsquery); each source is
# cut to its best CANDIDATES by the GIN index before anything is joined or paged. Every table is named with
# the organization too: RLS is the second wall, not the only one.
_QUERY = text("""
with q as (select websearch_to_tsquery('simple'::regconfig, cast(:q as text)) as tq),
req as (
  select d.id, ts_rank(d.search_vector, q.tq) as rank,
         to_tsvector('simple'::regconfig, d.title) @@ q.tq as in_title,
         to_tsvector('simple'::regconfig, d.description) @@ q.tq as in_description
  from decision_requests d, q
  where d.organization_id = :o and d.search_vector @@ q.tq
  order by rank desc, d.id limit :cap),
msg as (
  select m.decision_request_id as id, max(ts_rank(m.search_vector, q.tq)) as rank
  from messages m, q
  where m.organization_id = :o and m.search_vector @@ q.tq
  group by m.decision_request_id order by rank desc, m.decision_request_id limit :cap),
hits as (
  select coalesce(r.id, m.id) as id, greatest(r.rank, m.rank) as rank,
         coalesce(r.in_title, false) as in_title, coalesce(r.in_description, false) as in_description,
         m.id is not null as in_message
  from req r full join msg m on m.id = r.id),
win as (
  select d.id, d.title, d.status, d.effective_risk_level, d.created_at, h.rank, h.in_title,
         h.in_description, h.in_message,
         row_number() over (order by h.rank desc, d.created_at desc, d.id desc) as pos
  from hits h join decision_requests d on d.organization_id = :o and d.id = h.id
  order by pos limit :cap)
select (select count(*) from win) as total, p.*
from (select 1) one left join lateral (
  select * from win order by pos limit :limit offset :skip) p on true
""")


class PgSearch:
    """Postgres full text over the organization's own requests and discussion messages."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def query(self, organization_id: uuid.UUID, q: str, skip: int, limit: int) -> SearchPage:
        args = {"o": organization_id, "q": q, "cap": CANDIDATES, "skip": skip, "limit": limit}
        async with tenant_session(self.engine, organization_id) as s:
            rows = (await s.execute(_QUERY, args)).mappings().all()
        items = [
            SearchHit(
                decision_request_id=r["id"], title=r["title"], status=r["status"],
                effective_risk_level=r["effective_risk_level"], created_at=r["created_at"],
                matched_in=[
                    k
                    for k, hit in (
                        ("title", r["in_title"]),
                        ("description", r["in_description"]),
                        ("message", r["in_message"]),
                    )
                    if hit
                ],
                rank=float(r["rank"]),
            )
            for r in rows
            if r["id"] is not None  # the one row of an empty page carries only the total
        ]  # fmt: skip
        return SearchPage(items=items, total=int(rows[0]["total"]))
