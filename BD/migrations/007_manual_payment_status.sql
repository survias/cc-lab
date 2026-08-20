ALTER TABLE review_decisions
ADD COLUMN payment_review_status_new TEXT
CHECK (payment_review_status_new IN ('PAID_CONFIRMED', 'UNPAID_CONFIRMED'));

UPDATE review_decisions
SET payment_review_status_new = payment_review_status;

ALTER TABLE review_decisions DROP COLUMN payment_review_status;

ALTER TABLE review_decisions
RENAME COLUMN payment_review_status_new TO payment_review_status;
