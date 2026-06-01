-- 会话表
create table qa_session (
    id bigint primary key auto_increment,
    user_id bigint not null,
    title varchar(200) comment '会话标题（取第一条问题）',
    create_time datetime,
    update_time datetime,
    create_user bigint,
    update_user bigint
);

-- qa_history 加 session_id 列
alter table qa_history add column session_id bigint default null comment '所属会话ID';
