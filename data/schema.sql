-- TBX finance-assistant schema (MySQL).
-- Base DDL is from "TBX - Database Schema.md"; the indexes at the bottom are added,
-- because every date-range question filters on transaction_date and the base DDL
-- indexes only the primary keys and the account_id foreign key.

DROP TABLE IF EXISTS `transaction`;
DROP TABLE IF EXISTS account;
DROP TABLE IF EXISTS bank;

CREATE TABLE bank (
    bank_code    VARCHAR(10)  PRIMARY KEY,
    bank_name    VARCHAR(150) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE account (
    account_id         VARCHAR(36)  PRIMARY KEY,
    entity_id          VARCHAR(36)  NOT NULL,
    account_number     VARCHAR(20)  NOT NULL,
    program_id         INT          NOT NULL,
    available_balance  DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    bank_code          VARCHAR(10)  NOT NULL,
    FOREIGN KEY (bank_code) REFERENCES bank(bank_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `transaction` (
    transaction_id           VARCHAR(36)  PRIMARY KEY,
    account_id               VARCHAR(36)  NOT NULL,
    transaction_date         DATETIME(6)  NOT NULL,
    transaction_type         ENUM('credit','debit') NOT NULL,
    description              VARCHAR(500) DEFAULT NULL,
    transaction_amount       DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    transaction_reference_id VARCHAR(64)  DEFAULT NULL,
    utr_number               VARCHAR(256) DEFAULT NULL,
    FOREIGN KEY (account_id) REFERENCES account(account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- CHANGED FROM THE ORIGINAL DDL: transaction_date is DATETIME(6), not TIMESTAMP(6).
-- MySQL converts TIMESTAMP to UTC on write and back to the session timezone on read.
-- If the app session and the DB session disagree on timezone, transactions near a month
-- boundary land in the wrong month and every "last month" total is quietly wrong.
-- DATETIME stores the literal value. If you must keep TIMESTAMP, pin the session with
--   SET time_zone = '+05:30';
-- on every connection.

CREATE INDEX idx_txn_date      ON `transaction` (transaction_date);
CREATE INDEX idx_txn_acct_date ON `transaction` (account_id, transaction_date);
CREATE INDEX idx_txn_type_date ON `transaction` (transaction_type, transaction_date);
CREATE INDEX idx_txn_ref       ON `transaction` (transaction_reference_id);
CREATE INDEX idx_txn_utr       ON `transaction` (utr_number(64));
CREATE INDEX idx_acct_bank     ON account (bank_code);
CREATE INDEX idx_acct_entity   ON account (entity_id);
