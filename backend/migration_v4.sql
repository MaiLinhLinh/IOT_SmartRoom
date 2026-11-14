USE aiot_db;
GO

-- =====================================================
-- MIGRATION v4 — 1 quạt 3 mức → 3 quạt độc lập (bật/tắt)
-- fan_level 0-3 bị XÓA KHỎI TẤT CẢ CÁC BẢNG (không tương thích log cũ).
-- Giữ nguyên dữ liệu khác (users, log cũ còn lại các cột khác).
--
-- Chạy thủ công trong SSMS, hoặc dùng:
--   python init_db.py --reset --admin-username admin --admin-password admin123
-- (init_db --reset sẽ DROP toàn bộ bảng theo models.py v4)
-- =====================================================

-- 1. device_state: bỏ fan_level, thêm 3 cờ quạt
IF COL_LENGTH('dbo.device_state', 'fan_level') IS NOT NULL
    ALTER TABLE dbo.device_state DROP COLUMN fan_level;
GO
ALTER TABLE dbo.device_state ADD
    fan1 BIT NOT NULL DEFAULT 0,
    fan2 BIT NOT NULL DEFAULT 0,
    fan3 BIT NOT NULL DEFAULT 0;
GO

-- 2. sensor_logs: tương tự
IF COL_LENGTH('dbo.sensor_logs', 'fan_level') IS NOT NULL
    ALTER TABLE dbo.sensor_logs DROP COLUMN fan_level;
GO
ALTER TABLE dbo.sensor_logs ADD
    fan1 BIT NOT NULL DEFAULT 0,
    fan2 BIT NOT NULL DEFAULT 0,
    fan3 BIT NOT NULL DEFAULT 0;
GO

-- 3. control_logs: bỏ fan_level + fan_level_prev, thêm 3 cờ + 3 giá trị trước đó
IF COL_LENGTH('dbo.control_logs', 'fan_level') IS NOT NULL
    ALTER TABLE dbo.control_logs DROP COLUMN fan_level;
GO
IF COL_LENGTH('dbo.control_logs', 'fan_level_prev') IS NOT NULL
    ALTER TABLE dbo.control_logs DROP COLUMN fan_level_prev;
GO
ALTER TABLE dbo.control_logs ADD
    fan1      BIT NOT NULL DEFAULT 0,
    fan2      BIT NOT NULL DEFAULT 0,
    fan3      BIT NOT NULL DEFAULT 0,
    fan1_prev BIT NULL,
    fan2_prev BIT NULL,
    fan3_prev BIT NULL;
GO

PRINT 'Migration v4 hoan thanh: 3 quat doc lap (fan1/fan2/fan3).';
GO
