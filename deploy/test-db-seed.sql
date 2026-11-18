-- 本地测试用数据库（仅测试环境，请勿在生产执行）
-- 用途：让 tests/test_connectivity.py、tests/test_query_endpoint.py、
--       tests/test_target_connections.py 在本机可直接运行。
-- 执行：mysql -uroot -p < deploy/test-db-seed.sql
--
-- 生成的对象：
--   库   synetra_test        （测试专用库，名字即标注）
--   账号 synetra / synetra_pass（仅授予 synetra_test.*）
--   表   synetra_test.orders （25 行样例数据，供分页/限行用例断言）

CREATE DATABASE IF NOT EXISTS `synetra_test`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;

CREATE USER IF NOT EXISTS 'synetra'@'localhost' IDENTIFIED BY 'synetra_pass';
CREATE USER IF NOT EXISTS 'synetra'@'127.0.0.1' IDENTIFIED BY 'synetra_pass';
ALTER USER 'synetra'@'localhost' IDENTIFIED BY 'synetra_pass';
ALTER USER 'synetra'@'127.0.0.1' IDENTIFIED BY 'synetra_pass';

GRANT ALL PRIVILEGES ON `synetra_test`.* TO 'synetra'@'localhost';
GRANT ALL PRIVILEGES ON `synetra_test`.* TO 'synetra'@'127.0.0.1';
FLUSH PRIVILEGES;

USE `synetra_test`;

DROP TABLE IF EXISTS `orders`;
CREATE TABLE `orders` (
  `id`   INT PRIMARY KEY,
  `name` VARCHAR(64),
  `qty`  INT
);

INSERT INTO `orders` (`id`, `name`, `qty`) VALUES
  (1,  'it-order-1',  3),  (2,  'it-order-2',  6),  (3,  'it-order-3',  9),
  (4,  'it-order-4',  12), (5,  'it-order-5',  15), (6,  'it-order-6',  18),
  (7,  'it-order-7',  21), (8,  'it-order-8',  24), (9,  'it-order-9',  27),
  (10, 'it-order-10', 30), (11, 'it-order-11', 33), (12, 'it-order-12', 36),
  (13, 'it-order-13', 39), (14, 'it-order-14', 42), (15, 'it-order-15', 45),
  (16, 'it-order-16', 48), (17, 'it-order-17', 51), (18, 'it-order-18', 54),
  (19, 'it-order-19', 57), (20, 'it-order-20', 60), (21, 'it-order-21', 63),
  (22, 'it-order-22', 66), (23, 'it-order-23', 69), (24, 'it-order-24', 72),
  (25, 'it-order-25', 75);
