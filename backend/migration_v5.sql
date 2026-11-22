USE aiot_db;
GO

-- =====================================================
-- MIGRATION v5 — Cấu hình thiết bị (hãng + công suất) + Điện năng theo ngày
-- Mục đích:
--   1. device_info : người dùng nhập tên hãng quạt/điều hòa + công suất (W)
--   2. energy_daily: tổng hợp điện năng (Wh) mỗi ngày, từng thiết bị,
--                    để vẽ biểu đồ cột "mỗi ngày / mỗi thiết bị dùng bao nhiêu điện"
-- Công suất mặc định (khi power_watts NULL): quạt 45W, điều hòa 900W
--
-- ⚠️ Nếu chạy bằng sqlcmd mà tên tiếng Việt bị lỗi dấu, thêm -f 65001:
--   sqlcmd -S localhost -E -d aiot_db -f 65001 -i migration_v5.sql
-- (Hoặc chỉ cần chạy server: lifespan sẽ tự tạo bảng + seed 4 thiết bị.)
-- =====================================================

-- 1. device_info — 4 thiết bị cố định: fan1, fan2, fan3, ac
IF OBJECT_ID('dbo.device_info', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.device_info (
        device_key  NVARCHAR(10)  NOT NULL PRIMARY KEY,  -- fan1 | fan2 | fan3 | ac
        device_name NVARCHAR(100) NOT NULL,
        brand       NVARCHAR(100) NULL,                 -- tên hãng người dùng nhập
        power_watts FLOAT         NULL,                 -- công suất danh định (W); NULL = dùng mặc định
        updated_at  DATETIME      NULL
    );
END
GO

-- Seed 4 thiết bị (giá trị power thực tế đang dùng: quạt 45W, điều hòa 900W)
IF NOT EXISTS (SELECT 1 FROM dbo.device_info WHERE device_key = 'fan1')
    INSERT INTO dbo.device_info (device_key, device_name, brand, power_watts)
    VALUES ('fan1', N'Quạt 1', NULL, 45);
IF NOT EXISTS (SELECT 1 FROM dbo.device_info WHERE device_key = 'fan2')
    INSERT INTO dbo.device_info (device_key, device_name, brand, power_watts)
    VALUES ('fan2', N'Quạt 2', NULL, 45);
IF NOT EXISTS (SELECT 1 FROM dbo.device_info WHERE device_key = 'fan3')
    INSERT INTO dbo.device_info (device_key, device_name, brand, power_watts)
    VALUES ('fan3', N'Quạt 3', NULL, 45);
IF NOT EXISTS (SELECT 1 FROM dbo.device_info WHERE device_key = 'ac')
    INSERT INTO dbo.device_info (device_key, device_name, brand, power_watts)
    VALUES ('ac', N'Điều hòa', NULL, 900);
GO

-- 2. energy_daily — tổng hợp Wh theo ngày (đơn vị Wh, frontend tự /1000 nếu muốn kWh)
IF OBJECT_ID('dbo.energy_daily', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.energy_daily (
        [date]    DATE  NOT NULL PRIMARY KEY,
        fan1_wh   FLOAT NOT NULL DEFAULT 0,
        fan2_wh   FLOAT NOT NULL DEFAULT 0,
        fan3_wh   FLOAT NOT NULL DEFAULT 0,
        ac_wh     FLOAT NOT NULL DEFAULT 0,
        total_wh  FLOAT NOT NULL DEFAULT 0
    );
END
GO

PRINT 'Migration v5 hoan thanh: device_info + energy_daily.';
GO
