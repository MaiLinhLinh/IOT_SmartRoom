USE aiot_db;
GO

-- =====================================================
-- MIGRATION v2 - Chạy trong SSMS để cập nhật DB
-- =====================================================

-- 1. MỞ RỘNG BẢNG users (Trang 5 Profile)
-- Chạy từng dòng ALTER nếu cột chưa tồn tại
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('users') AND name='full_name')
    ALTER TABLE users ADD full_name NVARCHAR(100) NULL;

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('users') AND name='email')
    ALTER TABLE users ADD email NVARCHAR(100) NULL;

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('users') AND name='created_at')
    ALTER TABLE users ADD created_at DATETIME DEFAULT GETDATE();

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('users') AND name='role')
    ALTER TABLE users ADD role NVARCHAR(20) DEFAULT 'user';
GO

-- 2. MỞ RỘNG device_state (Dashboard - AI reason + số người)
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('device_state') AND name='ai_reason')
    ALTER TABLE device_state ADD ai_reason NVARCHAR(500) NULL;

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('device_state') AND name='people_count')
    ALTER TABLE device_state ADD people_count INT DEFAULT 0;
GO

-- 3. TẠO BẢNG control_logs (Trang 6 - Lịch sử thao tác)
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id=OBJECT_ID('control_logs') AND type='U')
BEGIN
    CREATE TABLE control_logs (
        id              INT           IDENTITY(1,1) PRIMARY KEY,
        timestamp       DATETIME      DEFAULT GETDATE(),
        actor           NVARCHAR(50)  NOT NULL,
        -- actor: 'user:admin' hoặc 'AI'
        actor_type      NVARCHAR(10)  NOT NULL,
        -- actor_type: 'USER' hoặc 'AI'
        description     NVARCHAR(500) NOT NULL,
        -- description: 'Bật quạt mức 2, tắt điều hòa'
        fan_level       INT           NOT NULL,
        ac_on           BIT           NOT NULL,
        ac_temp         INT           NOT NULL,
        mode            NVARCHAR(10)  NOT NULL,
        fan_level_prev  INT           NULL,
        ac_on_prev      BIT           NULL,
        ac_temp_prev    INT           NULL,
        ai_reason       NVARCHAR(500) NULL
    );
    PRINT 'Tao bang control_logs thanh cong';
END
GO

PRINT 'Migration v2 hoan thanh!';
