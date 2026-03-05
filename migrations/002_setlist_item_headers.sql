-- Phase 5: Allow setlist items to be headers/notes rather than arrangements
-- Also add pco_service_type_id for Phase 6

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'setlist_items' AND column_name = 'item_type'
  ) THEN
    ALTER TABLE setlist_items
      ADD COLUMN item_type TEXT NOT NULL DEFAULT 'song',
      ADD COLUMN label TEXT;
    ALTER TABLE setlist_items ALTER COLUMN arrangement_id DROP NOT NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'setlists' AND column_name = 'pco_service_type_id'
  ) THEN
    ALTER TABLE setlists ADD COLUMN pco_service_type_id TEXT;
  END IF;
END $$;
