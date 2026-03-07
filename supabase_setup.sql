-- ═══════════════════════════════════════════════════════════════════
--  AniChapters — Shared Database Setup
--  Run this ONCE in Supabase → SQL Editor → New Query
-- ═══════════════════════════════════════════════════════════════════

-- ── 1. الجدول الرئيسي ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shared_chapters (
    id              BIGSERIAL       PRIMARY KEY,

    -- مفتاح البحث الثلاثي (AniList ID + season + episode)
    anime_id        INTEGER         NOT NULL,
    anime_title     TEXT            NOT NULL DEFAULT '',
    season_number   INTEGER         NOT NULL DEFAULT 1,
    episode_number  INTEGER         NOT NULL,

    -- الشابترات مُخزَّنة كـ JSONB للبحث الداخلي السريع
    -- التنسيق: [{"timestamp_ms": 0, "name": "Cold Open", "source": "audio"}, …]
    chapters_json   JSONB           NOT NULL DEFAULT '[]',

    -- مستوى الثقة: high | medium | low | fallback
    confidence      TEXT            NOT NULL DEFAULT 'medium'
        CHECK (confidence IN ('high', 'medium', 'low', 'fallback')),

    -- كم مرة جرى تحميل هذا السجل
    use_count       INTEGER         NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),

    -- يمنع تكرار نفس الحلقة
    CONSTRAINT uq_shared_chapters UNIQUE (anime_id, season_number, episode_number)
);

-- ── 2. Index للبحث السريع ─────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_sc_lookup
    ON shared_chapters (anime_id, season_number, episode_number);

CREATE INDEX IF NOT EXISTS idx_sc_updated
    ON shared_chapters (updated_at DESC);

-- ── 3. Trigger — يحدّث updated_at تلقائياً ──────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sc_updated_at ON shared_chapters;
CREATE TRIGGER trg_sc_updated_at
    BEFORE UPDATE ON shared_chapters
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── 4. RLS (Row Level Security) ───────────────────────────────────
-- السياسة: أي شخص يقرأ، أي شخص يكتب/يحدّث (open community DB)
ALTER TABLE shared_chapters ENABLE ROW LEVEL SECURITY;

-- قراءة مفتوحة لجميع المستخدمين (بما فيهم anon)
DROP POLICY IF EXISTS "allow_public_read" ON shared_chapters;
CREATE POLICY "allow_public_read"
    ON shared_chapters FOR SELECT
    TO anon, authenticated
    USING (true);

-- كتابة/تحديث مفتوحة (open contribution model)
DROP POLICY IF EXISTS "allow_public_insert" ON shared_chapters;
CREATE POLICY "allow_public_insert"
    ON shared_chapters FOR INSERT
    TO anon, authenticated
    WITH CHECK (true);

DROP POLICY IF EXISTS "allow_public_update" ON shared_chapters;
CREATE POLICY "allow_public_update"
    ON shared_chapters FOR UPDATE
    TO anon, authenticated
    USING (true)
    WITH CHECK (true);

-- ── 5. View مساعدة للإحصائيات ────────────────────────────────────
CREATE OR REPLACE VIEW shared_chapters_stats AS
SELECT
    COUNT(*)            AS total_episodes,
    SUM(use_count)      AS total_hits,
    MAX(updated_at)     AS last_updated,
    COUNT(*) FILTER (WHERE confidence = 'high')     AS high_confidence,
    COUNT(*) FILTER (WHERE confidence = 'medium')   AS medium_confidence,
    COUNT(*) FILTER (WHERE confidence = 'low')      AS low_confidence
FROM shared_chapters;

-- ── 6. Function لـ upsert + increment use_count ──────────────────
-- تُستدعى عند الـ lookup لزيادة العداد بشكل atomic
CREATE OR REPLACE FUNCTION increment_use_count(
    p_anime_id      INTEGER,
    p_season        INTEGER,
    p_episode       INTEGER
)
RETURNS void AS $$
BEGIN
    UPDATE shared_chapters
    SET use_count = use_count + 1
    WHERE anime_id      = p_anime_id
      AND season_number = p_season
      AND episode_number = p_episode;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ── 7. تحقق من النتيجة ───────────────────────────────────────────
SELECT 'Setup complete. Table and policies created successfully.' AS status;
