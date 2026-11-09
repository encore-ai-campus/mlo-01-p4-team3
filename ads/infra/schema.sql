-- D-06-02 MySQL 8.4 DDL reference. Deploy with ads/manage.py migrate.
-- Generated from ads_web migrations 0002 through 0004 (ledger, pending, optimization versions).
--
-- Create model AdReceipt
--
CREATE TABLE `ads_web_adreceipt` (`digest` varchar(64) NOT NULL PRIMARY KEY, `first_received_ms` bigint UNSIGNED NOT NULL CHECK (`first_received_ms` >= 0), `result` json NULL);
--
-- Create model AdSession
--
CREATE TABLE `ads_web_adsession` (`session_id` varchar(128) COLLATE `utf8mb4_0900_bin` NOT NULL PRIMARY KEY, `last_click_ms` bigint UNSIGNED NULL CHECK (`last_click_ms` >= 0));
--
-- Create model AdDailyBudget
--
CREATE TABLE `ads_web_addailybudget` (`id` bigint AUTO_INCREMENT NOT NULL PRIMARY KEY, `campaign_id` varchar(100) COLLATE `utf8mb4_0900_bin` NOT NULL, `attribution_day` date NOT NULL, `committed_mw` bigint UNSIGNED NOT NULL CHECK (`committed_mw` >= 0), CONSTRAINT `ad_budget_campaign_day` UNIQUE (`campaign_id`, `attribution_day`));
--
-- Create model AdOutbox
--
CREATE TABLE `ads_web_adoutbox` (`id` bigint AUTO_INCREMENT NOT NULL PRIMARY KEY, `event_id` varchar(240) COLLATE `utf8mb4_0900_bin` NOT NULL UNIQUE, `topic` varchar(32) NOT NULL, `message_key` varchar(160) COLLATE `utf8mb4_0900_bin` NULL, `payload` json NOT NULL, `published_at` datetime(6) NULL);
--
-- Create model AdStoredRequest
--
CREATE TABLE `ads_web_adstoredrequest` (`request_id` varchar(128) COLLATE `utf8mb4_0900_bin` NOT NULL PRIMARY KEY, `request` json NOT NULL, `response` json NULL, `serve_id` varchar(128) COLLATE `utf8mb4_0900_bin` NULL UNIQUE, `serve_data` json NULL, `session_id` varchar(128) COLLATE `utf8mb4_0900_bin` NOT NULL, `creative_id` varchar(100) COLLATE `utf8mb4_0900_bin` NULL, `candidates` json NOT NULL, `impressed` bool NOT NULL, `clicked` bool NOT NULL);
--
-- Create model AdLedgerEntry
--
CREATE TABLE `ads_web_adledgerentry` (`charge_id` varchar(160) COLLATE `utf8mb4_0900_bin` NOT NULL PRIMARY KEY, `amount_mw` bigint NOT NULL, `state` varchar(10) NOT NULL, `source_event_id` varchar(220) COLLATE `utf8mb4_0900_bin` NULL, `source` varchar(10) NOT NULL, `kind` varchar(6) NOT NULL, `slot` varchar(20) NOT NULL, `billing` varchar(3) NOT NULL, `price_mw` bigint UNSIGNED NOT NULL CHECK (`price_mw` >= 0), `release_after_ms` bigint UNSIGNED NOT NULL CHECK (`release_after_ms` >= 0), `budget_id` bigint NOT NULL, CONSTRAINT `ad_ledger_signed_state` CHECK (((`amount_mw` > 0 AND `kind` = 'charge' AND `state` IN ('reserved', 'confirmed', 'released')) OR (`amount_mw` < 0 AND `kind` = 'refund' AND `state` = 'confirmed'))));
CREATE INDEX `ad_outbox_pending` ON `ads_web_adoutbox` (`published_at`, `id`);
CREATE INDEX `ad_serve_frequency` ON `ads_web_adstoredrequest` (`session_id`, `creative_id`);
ALTER TABLE `ads_web_adledgerentry` ADD CONSTRAINT `ads_web_adledgerentr_budget_id_db58789f_fk_ads_web_a` FOREIGN KEY (`budget_id`) REFERENCES `ads_web_addailybudget` (`id`);
CREATE INDEX `ad_ledger_expiry` ON `ads_web_adledgerentry` (`state`, `release_after_ms`);
--
-- Add field pending_after_ms to adreceipt
--
ALTER TABLE `ads_web_adreceipt` ADD COLUMN `pending_after_ms` bigint UNSIGNED DEFAULT 0 NOT NULL CHECK (`pending_after_ms` >= 0);
ALTER TABLE `ads_web_adreceipt` ALTER COLUMN `pending_after_ms` DROP DEFAULT;
--
-- Add field pending_input to adreceipt
--
ALTER TABLE `ads_web_adreceipt` ADD COLUMN `pending_input` json NULL;
CREATE INDEX `ads_web_adreceipt_pending_after_ms_53d652a9` ON `ads_web_adreceipt` (`pending_after_ms`);
--
-- Create model AdOptimizationVersion
--
CREATE TABLE `ads_web_adoptimizationversion` (`id` bigint AUTO_INCREMENT NOT NULL PRIMARY KEY, `kind` varchar(6) NOT NULL, `version` varchar(80) COLLATE `utf8mb4_0900_bin` NOT NULL UNIQUE, `source_version` varchar(200) COLLATE `utf8mb4_0900_bin` NOT NULL, `as_of_ms` bigint UNSIGNED NOT NULL CHECK (`as_of_ms` >= 0), `payload` json NOT NULL, CONSTRAINT `ad_optimization_revision` UNIQUE (`kind`, `source_version`, `as_of_ms`));
CREATE INDEX `ad_optimization_latest` ON `ads_web_adoptimizationversion` (`kind`, `as_of_ms`);
