USE aiot_db;
GO

-- =====================================================
-- MIGRATION v3 — RESET TOÀN BỘ SCHEMA (mất dữ liệu cũ)
-- Cách khuyến nghị: python init_db.py --reset --admin-username admin --admin-password admin123
-- File này dành cho ai muốn chạy thủ công trong SSMS.
-- =====================================================

-- 1. Xoá bảng cũ (thứ tự: bảng có FK trước)
IF OBJECT_ID('dbo.control_logs', 'U') IS NOT NULL DROP TABLE dbo.control_logs;
IF OBJECT_ID('dbo.sensor_logs',  'U') IS NOT NULL DROP TABLE dbo.sensor_logs;
IF OBJECT_ID('dbo.device_state', 'U') IS NOT NULL DROP TABLE dbo.device_state;
IF OBJECT_ID('dbo.users',        'U') IS NOT NULL DROP TABLE dbo.users;
GO

-- 2. users
CREATE TABLE dbo.users (
    id          INT IDENTITY(1,1) PRIMARY KEY,
    username    NVARCHAR(50)  NOT NULL UNIQUE,
    password    NVARCHAR(255) NOT NULL,
    full_name   NVARCHAR(100) NULL,
    email       NVARCHAR(100) NULL,
    created_at  DATETIME      NOT NULL DEFAULT GETDATE(),
    role        NVARCHAR(20)  NOT NULL DEFAULT 'user'
);
GO

-- 3. device_state (đúng 1 row id=1)
CREATE TABLE dbo.device_state (
    id                INT           NOT NULL DEFAULT 1 PRIMARY KEY,
    fan_level         INT           NOT NULL DEFAULT 0,
    ac_on             BIT           NOT NULL DEFAULT 0,
    ac_temp           INT           NOT NULL DEFAULT 26,
    mode              NVARCHAR(10)  NOT NULL DEFAULT 'MANUAL',
    updated_at        DATETIME      DEFAULT GETDATE(),
    ai_reason         NVARCHAR(500) NULL,
    people_count      INT           NOT NULL DEFAULT 0,
    people_source     NVARCHAR(20)  NOT NULL DEFAULT 'NONE',
    people_updated_at DATETIME      NULL
);
GO

-- 4. sensor_logs
CREATE TABLE dbo.sensor_logs (
    id            INT IDENTITY(1,1) PRIMARY KEY,
    timestamp     DATETIME      NOT NULL DEFAULT GETDATE(),
    temperature   FLOAT         NULL,
    humidity      FLOAT         NULL,
    people_count  INT           NOT NULL DEFAULT 0,
    fan_level     INT           NOT NULL DEFAULT 0,
    ac_on         BIT           NOT NULL DEFAULT 0,
    ac_temp       INT           NOT NULL DEFAULT 26,
    mode          NVARCHAR(10)  NOT NULL DEFAULT 'MANUAL'
);
CREATE INDEX IX_sensor_logs_timestamp ON dbo.sensor_logs (timestamp DESC);
GO

-- 5. control_logs
CREATE TABLE dbo.control_logs (
    id             INT IDENTITY(1,1) PRIMARY KEY,
    timestamp      DATETIME      NOT NULL DEFAULT GETDATE(),
    actor          NVARCHAR(50)  NOT NULL,
    actor_type     NVARCHAR(10)  NOT NULL,
    actor_user_id  INT           NULL,
    source         NVARCHAR(20)  NOT NULL DEFAULT 'MANUAL',
    description    NVARCHAR(500) NOT NULL,
    fan_level      INT           NOT NULL,
    ac_on          BIT           NOT NULL,
    ac_temp        INT           NOT NULL,
    mode           NVARCHAR(10)  NOT NULL,
    fan_level_prev INT           NULL,
    ac_on_prev     BIT           NULL,
    ac_temp_prev   INT           NULL,
    ai_reason      NVARCHAR(500) NULL,
    temperature    FLOAT         NULL,
    humidity       FLOAT         NULL,
    people_count   INT           NULL,
    CONSTRAINT FK_control_logs_users
        FOREIGN KEY (actor_user_id) REFERENCES dbo.users(id) ON DELETE SET NULL
);
CREATE INDEX IX_control_logs_timestamp  ON dbo.control_logs (timestamp DESC);
CREATE INDEX IX_control_logs_actor_type ON dbo.control_logs (actor_type);
GO

-- 6. Seed trạng thái thiết bị
INSERT INTO dbo.device_state (id) VALUES (1);
GO

PRINT 'Migration v3 hoan thanh!';
