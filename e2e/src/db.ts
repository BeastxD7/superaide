// Direct access to this slot's database: wipe it between tests and check what
// the app stored. Only ever touches databases named superaide_e2e_<slot>.
import pg from 'pg'
import { DB_NAME, PG_URL } from './env'

function guard(): void {
  if (!DB_NAME.startsWith('superaide_e2e_')) throw new Error(`Refusing to touch database ${DB_NAME}`)
}

export async function query<T extends pg.QueryResultRow = pg.QueryResultRow>(sql: string, params: unknown[] = []): Promise<T[]> {
  guard()
  const client = new pg.Client({ connectionString: PG_URL })
  await client.connect()
  try {
    return (await client.query<T>(sql, params)).rows
  } finally {
    await client.end()
  }
}

// Empty every table (the schema and migrations stay), so each test starts from
// a fresh install: no accounts, nobody has hired Ava yet.
export async function resetDatabase(): Promise<void> {
  const tables = await query<{ tablename: string }>(
    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'",
  )
  if (!tables.length) return
  const sql = `TRUNCATE ${tables.map((t) => `"${t.tablename}"`).join(', ')} RESTART IDENTITY CASCADE`
  // The API's background worker keeps reading these tables; when Postgres resolves a
  // deadlock with it by cancelling the TRUNCATE (SQLSTATE 40P01), try again.
  for (let attempt = 1; ; attempt++) {
    try {
      await query(sql)
      return
    } catch (e) {
      if ((e as { code?: string }).code !== '40P01' || attempt >= 5) throw e
      await new Promise((r) => setTimeout(r, 100 * attempt))
    }
  }
}
