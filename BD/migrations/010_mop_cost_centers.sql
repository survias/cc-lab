UPDATE cost_centers
SET subcategory_name = 'Goods & Rights', is_active = 1
WHERE category_code = 500 AND subcategory_code = 501;

UPDATE cost_centers
SET subcategory_name = 'Pre-existing Infrastructure', is_active = 1
WHERE category_code = 500 AND subcategory_code = 502;

UPDATE cost_centers
SET subcategory_name = 'Toll Revenue Sharing', is_active = 1
WHERE category_code = 500 AND subcategory_code = 503;

UPDATE cost_centers
SET subcategory_name = 'Legacy MOP center', is_active = 0
WHERE category_code = 500 AND subcategory_code = 504;

UPDATE cost_centers
SET subcategory_name = 'Others MOP', is_active = 1
WHERE category_code = 500 AND subcategory_code = 505;
