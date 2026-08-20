INSERT INTO cost_centers(
    category_code,
    subcategory_code,
    category_name,
    subcategory_name,
    is_active
)
VALUES (100, 109, 'EPC - Contractor', 'Contract Management', 1)
ON CONFLICT(category_code, subcategory_code) DO UPDATE SET
    category_name = excluded.category_name,
    subcategory_name = excluded.subcategory_name,
    is_active = 1;
