"""Tiny dict-based i18n. English / 华语 / Bahasa Melayu. No dependencies."""
from flask import g

LANGS = {"en": "English", "zh": "华语", "ms": "Bahasa Melayu"}

STRINGS = {
    # ── generic ──
    "app_name": {"en": "Tuition System", "zh": "补习管理系统", "ms": "Sistem Tuisyen"},
    "save": {"en": "Save", "zh": "保存", "ms": "Simpan"},
    "cancel": {"en": "Cancel", "zh": "取消", "ms": "Batal"},
    "add": {"en": "Add", "zh": "新增", "ms": "Tambah"},
    "edit": {"en": "Edit", "zh": "编辑", "ms": "Sunting"},
    "delete": {"en": "Delete", "zh": "删除", "ms": "Padam"},
    "back": {"en": "Back", "zh": "返回", "ms": "Kembali"},
    "search": {"en": "Search", "zh": "搜索", "ms": "Cari"},
    "filter": {"en": "Filter", "zh": "筛选", "ms": "Tapis"},
    "all": {"en": "All", "zh": "全部", "ms": "Semua"},
    "none": {"en": "None", "zh": "无", "ms": "Tiada"},
    "actions": {"en": "Actions", "zh": "操作", "ms": "Tindakan"},
    "confirm_delete": {"en": "Delete this? This cannot be undone.", "zh": "确定删除？无法恢复。", "ms": "Padam ini? Tidak boleh dibatalkan."},
    "saved": {"en": "Saved.", "zh": "已保存。", "ms": "Disimpan."},
    "deleted": {"en": "Deleted.", "zh": "已删除。", "ms": "Dipadam."},
    "nothing_here": {"en": "Nothing here yet.", "zh": "还没有资料。", "ms": "Belum ada apa-apa."},
    "remarks": {"en": "Remarks", "zh": "备注", "ms": "Catatan"},
    "optional": {"en": "optional", "zh": "可选", "ms": "pilihan"},
    "close_month": {"en": "Close Month", "zh": "月度结算", "ms": "Tutup Bulan"},

    # ── nav ──
    "nav_dashboard": {"en": "Dashboard", "zh": "总览", "ms": "Papan Pemuka"},
    "nav_today": {"en": "Today", "zh": "今天", "ms": "Hari Ini"},
    "todays_attendance": {"en": "Today's Attendance", "zh": "今天的出席", "ms": "Kehadiran Hari Ini"},
    "no_class_today": {"en": "No classes on this day.", "zh": "这天没有课。", "ms": "Tiada kelas pada hari ini."},
    "today_btn": {"en": "Today", "zh": "回到今天", "ms": "Hari ini"},
    "nav_students": {"en": "Students", "zh": "学生", "ms": "Pelajar"},
    "nav_classes": {"en": "Classes", "zh": "班级", "ms": "Kelas"},
    "nav_attendance": {"en": "Attendance", "zh": "出席", "ms": "Kehadiran"},
    "nav_finance": {"en": "Finance", "zh": "财务", "ms": "Kewangan"},
    "nav_bills": {"en": "Bills", "zh": "月度账单", "ms": "Bil"},
    "nav_reports": {"en": "Reports", "zh": "报告", "ms": "Laporan"},
    "nav_settings": {"en": "Settings", "zh": "设置", "ms": "Tetapan"},
    "logout": {"en": "Log out", "zh": "登出", "ms": "Log keluar"},

    # ── auth ──
    "login": {"en": "Log in", "zh": "登入", "ms": "Log masuk"},
    "create_account": {"en": "Create account", "zh": "创建账号", "ms": "Cipta akaun"},
    "password": {"en": "Password", "zh": "密码", "ms": "Kata laluan"},
    "confirm_password": {"en": "Confirm password", "zh": "确认密码", "ms": "Sahkan kata laluan"},
    "teacher_name": {"en": "Your name", "zh": "你的名字", "ms": "Nama anda"},
    "wrong_password": {"en": "Wrong password.", "zh": "密码错误。", "ms": "Kata laluan salah."},
    "welcome_setup": {"en": "Set up your account", "zh": "设置你的账号", "ms": "Sediakan akaun anda"},
    "password_mismatch": {"en": "Passwords do not match.", "zh": "两次密码不一致。", "ms": "Kata laluan tidak sepadan."},

    # ── dashboard ──
    "current_month": {"en": "Current month", "zh": "当前月份", "ms": "Bulan semasa"},
    "quick_add": {"en": "Quick Add", "zh": "快速新增", "ms": "Tambah Pantas"},
    "student_overview": {"en": "Student Overview", "zh": "学生概况", "ms": "Ringkasan Pelajar"},
    "class_overview": {"en": "Class Overview", "zh": "班级概况", "ms": "Ringkasan Kelas"},
    "total_students": {"en": "Total Students", "zh": "学生总数", "ms": "Jumlah Pelajar"},
    "active_students": {"en": "Active Students", "zh": "在籍学生", "ms": "Pelajar Aktif"},
    "trial_students": {"en": "Trial Students", "zh": "试课学生", "ms": "Pelajar Percubaan"},
    "new_this_month": {"en": "New This Month", "zh": "本月新生", "ms": "Baharu Bulan Ini"},
    "leaving_this_month": {"en": "Leaving This Month", "zh": "本月离开", "ms": "Berhenti Bulan Ini"},
    "total_classes": {"en": "Total Classes", "zh": "班级总数", "ms": "Jumlah Kelas"},
    "active_classes": {"en": "Active Classes", "zh": "活跃班级", "ms": "Kelas Aktif"},
    "todays_classes": {"en": "Today's Classes", "zh": "今日课程", "ms": "Kelas Hari Ini"},
    "students_today": {"en": "Students Today", "zh": "今日学生", "ms": "Pelajar Hari Ini"},
    "attendance_rate": {"en": "Attendance Rate", "zh": "出席率", "ms": "Kadar Kehadiran"},
    "present": {"en": "Present", "zh": "出席", "ms": "Hadir"},
    "absent": {"en": "Absent", "zh": "缺席", "ms": "Tidak Hadir"},
    "student_leave": {"en": "Student leave", "zh": "学生请假", "ms": "Cuti pelajar"},
    "teacher_leave": {"en": "Teacher leave", "zh": "老师请假", "ms": "Cuti guru"},
    "holiday": {"en": "Holiday", "zh": "假期", "ms": "Cuti"},
    "trial": {"en": "Trial", "zh": "试课", "ms": "Percubaan"},
    "replacement": {"en": "Make-up", "zh": "补课", "ms": "Ganti"},
    "excused": {"en": "Excused", "zh": "请假", "ms": "Dimaafkan"},
    "no_class": {"en": "No Class", "zh": "停课", "ms": "Tiada Kelas"},
    "makeup_arrange": {"en": "Make-up arrangement", "zh": "补课安排", "ms": "Aturan kelas ganti"},
    "mk_plan": {"en": "Tentative plan", "zh": "预计", "ms": "Rancangan sementara"},
    "mk_final": {"en": "Final decision", "zh": "最后决定", "ms": "Keputusan akhir"},
    "mk_none_opt": {"en": "— none —", "zh": "— 无 —", "ms": "— tiada —"},
    "mk_discussing": {"en": "Discussing", "zh": "讨论中", "ms": "Berbincang"},
    "mk_plan_date": {"en": "Tentative date", "zh": "预计日期", "ms": "Tarikh sementara"},
    "mk_undecided": {"en": "Not decided yet", "zh": "还没决定", "ms": "Belum diputuskan"},
    "mk_will": {"en": "Confirmed — make-up on", "zh": "确定补 · 日期", "ms": "Sah ganti · tarikh"},
    "mk_wont": {"en": "Confirmed — no make-up", "zh": "确定不补", "ms": "Sah tiada ganti"},
    "mk_clear": {"en": "Clear", "zh": "清除", "ms": "Kosongkan"},
    "mk_no": {"en": "No make-up", "zh": "不补", "ms": "Tiada ganti"},
    "expected_tuition": {"en": "Expected Tuition", "zh": "预计学费", "ms": "Yuran Dijangka"},
    "collected": {"en": "Collected", "zh": "已收", "ms": "Dikutip"},
    "outstanding": {"en": "Outstanding", "zh": "未收", "ms": "Tertunggak"},
    "estimated_income": {"en": "Estimated Income", "zh": "预测收入", "ms": "Pendapatan Anggaran"},
    "actual_income": {"en": "Actual Income", "zh": "实际收入", "ms": "Pendapatan Sebenar"},
    "collection_rate": {"en": "Collection Rate", "zh": "收款率", "ms": "Kadar Kutipan"},
    "quick_actions": {"en": "Quick Actions", "zh": "快捷操作", "ms": "Tindakan Pantas"},
    "fin_projected": {"en": "Projected", "zh": "本月预计", "ms": "Unjuran"},
    "fin_projected_hint": {"en": "if every class runs & everyone attends",
                           "zh": "全部照常上课、全员出席", "ms": "jika semua kelas berjalan"},
    "fin_due": {"en": "Due so far", "zh": "目前应收", "ms": "Perlu setakat ini"},
    "fin_due_hint": {"en": "from attendance recorded so far",
                     "zh": "根据已记录的出席", "ms": "dari kehadiran direkod"},
    "fin_collected_hint": {"en": "actually received", "zh": "实际已收到", "ms": "diterima"},
    "fin_outstanding_hint": {"en": "due so far − collected", "zh": "目前应收 − 已收", "ms": "perlu − dikutip"},

    # ── students ──
    "add_student": {"en": "Add Student", "zh": "新增学生", "ms": "Tambah Pelajar"},
    "name_zh": {"en": "Chinese Name", "zh": "华语名字", "ms": "Nama Cina"},
    "name_en": {"en": "English Name", "zh": "英文名字", "ms": "Nama Inggeris"},
    "phone": {"en": "Phone", "zh": "电话号码", "ms": "Telefon"},
    "age": {"en": "Age", "zh": "年龄", "ms": "Umur"},
    "gender": {"en": "Gender", "zh": "性别", "ms": "Jantina"},
    "dob": {"en": "Date of Birth", "zh": "出生日期", "ms": "Tarikh Lahir"},
    "school": {"en": "School", "zh": "学校", "ms": "Sekolah"},
    "grade": {"en": "Grade", "zh": "年级", "ms": "Tingkatan"},
    "address": {"en": "Address", "zh": "地址", "ms": "Alamat"},
    "status": {"en": "Status", "zh": "状态", "ms": "Status"},
    "joined_date": {"en": "Joined Date", "zh": "入学日期", "ms": "Tarikh Masuk"},
    "trial_date": {"en": "Trial Date", "zh": "试课日期", "ms": "Tarikh Percubaan"},
    "left_date": {"en": "Left Date", "zh": "离开日期", "ms": "Tarikh Berhenti"},
    "left_reason": {"en": "Reason for Leaving", "zh": "离开原因", "ms": "Sebab Berhenti"},
    "st_active": {"en": "Active", "zh": "在籍", "ms": "Aktif"},
    "st_trial": {"en": "Trial", "zh": "试课", "ms": "Percubaan"},
    "st_inactive": {"en": "Inactive", "zh": "暂停", "ms": "Tidak Aktif"},
    "st_left": {"en": "Left", "zh": "已离开", "ms": "Berhenti"},
    "student": {"en": "Student", "zh": "学生", "ms": "Pelajar"},
    "monthly_fee": {"en": "Monthly Fee", "zh": "月费", "ms": "Yuran Bulanan"},
    "payment_status": {"en": "Payment Status", "zh": "缴费状态", "ms": "Status Bayaran"},
    "tab_overview": {"en": "Overview", "zh": "概况", "ms": "Ringkasan"},
    "tab_parents": {"en": "Parents", "zh": "家长", "ms": "Ibu Bapa"},
    "tab_classes": {"en": "Classes", "zh": "班级", "ms": "Kelas"},
    "tab_finance": {"en": "Finance", "zh": "财务", "ms": "Kewangan"},
    "tab_results": {"en": "Results", "zh": "成绩", "ms": "Keputusan"},
    "tab_history": {"en": "Class History", "zh": "班级历史", "ms": "Sejarah Kelas"},
    "student_information": {"en": "Student Information", "zh": "学生资料", "ms": "Maklumat Pelajar"},
    "family": {"en": "Family", "zh": "家庭", "ms": "Keluarga"},
    "families": {"en": "Families", "zh": "家庭", "ms": "Keluarga"},
    "new_family": {"en": "New family (type a name)", "zh": "新家庭（输入名称）", "ms": "Keluarga baharu (taip nama)"},
    "no_family": {"en": "No family", "zh": "没有家庭", "ms": "Tiada keluarga"},
    "siblings": {"en": "Siblings", "zh": "兄弟姐妹", "ms": "Adik-beradik"},
    "manage_families": {"en": "Manage Families", "zh": "管理家庭", "ms": "Urus Keluarga"},
    "family_payment": {"en": "Record Family Payment", "zh": "记录整家收款", "ms": "Rekod Bayaran Keluarga"},
    "family_billing": {"en": "Family Billing", "zh": "整家收费", "ms": "Bil Keluarga"},
    "family_billing_hint": {"en": "One payment is split across all the siblings' fees for this month.",
                            "zh": "一笔款项会自动分摊到这个月全家孩子的学费上。",
                            "ms": "Satu bayaran dibahagikan antara yuran semua adik-beradik untuk bulan ini."},
    "members": {"en": "Members", "zh": "成员", "ms": "Ahli"},

    # ── parents ──
    "parent": {"en": "Parent", "zh": "家长", "ms": "Ibu Bapa"},
    "parent_n": {"en": "Parent", "zh": "家长", "ms": "Ibu Bapa"},
    "relationship": {"en": "Relationship", "zh": "关系", "ms": "Hubungan"},
    "email": {"en": "Email", "zh": "电邮", "ms": "E-mel"},

    # ── classes ──
    "add_class": {"en": "Add Class", "zh": "新增班级", "ms": "Tambah Kelas"},
    "class_name": {"en": "Class Name", "zh": "班级名称", "ms": "Nama Kelas"},
    "subject": {"en": "Subject", "zh": "科目", "ms": "Subjek"},
    "day": {"en": "Day", "zh": "星期", "ms": "Hari"},
    "time": {"en": "Time", "zh": "时间", "ms": "Masa"},
    "teacher": {"en": "Teacher", "zh": "老师", "ms": "Guru"},
    "location": {"en": "Room / Location", "zh": "地点", "ms": "Bilik / Lokasi"},
    "num_students": {"en": "Students", "zh": "学生人数", "ms": "Pelajar"},
    "monthly_revenue": {"en": "Monthly Revenue", "zh": "月收入", "ms": "Hasil Bulanan"},
    "fee_model": {"en": "Fee Model", "zh": "收费方式", "ms": "Model Yuran"},
    "fm_monthly": {"en": "Fixed monthly fee", "zh": "固定月费", "ms": "Yuran bulanan tetap"},
    "fm_per_lesson": {"en": "Per lesson", "zh": "按堂计费", "ms": "Setiap kelas"},
    "default_fee": {"en": "Default Fee", "zh": "默认收费", "ms": "Yuran Lalai"},
    "pricing": {"en": "Fee setup", "zh": "收费方式", "ms": "Cara yuran"},
    "pricing_q": {"en": "Student pricing", "zh": "学生收费", "ms": "Harga pelajar"},
    "pricing_same": {"en": "Same rate for every student", "zh": "所有学生统一价", "ms": "Kadar sama untuk semua"},
    "pricing_each": {"en": "Each student has their own rate", "zh": "每位学生各自定价", "ms": "Setiap pelajar kadar sendiri"},
    "month_word": {"en": "month", "zh": "月", "ms": "bulan"},
    "add_students_now": {"en": "Add students to this class", "zh": "加学生进这个班", "ms": "Tambah pelajar ke kelas ini"},
    "pick_existing": {"en": "Pick existing students", "zh": "选现有的学生", "ms": "Pilih pelajar sedia ada"},
    "new_students_label": {"en": "New students", "zh": "新学生", "ms": "Pelajar baharu"},
    "new_students_ph": {"en": "Amy Tan\nBen Lee", "zh": "陈美\n李明", "ms": "Amy Tan\nBen Lee"},
    "add_another_student": {"en": "Add another student", "zh": "再加一个学生", "ms": "Tambah pelajar lain"},
    "agent_required": {"en": "This billing mode needs an agent name.", "zh": "这个收费方式需要填写代理名字。", "ms": "Kaedah bil ini perlukan nama ejen."},
    "student_fee_all": {"en": "Fee for these students", "zh": "这些学生的学费", "ms": "Yuran pelajar ini"},
    "or_new_student": {"en": "…or a new student", "zh": "…或直接加新学生", "ms": "…atau pelajar baharu"},
    "new_student_ph": {"en": "type a name", "zh": "输入名字", "ms": "taip nama"},
    "fee_1v1_note": {"en": "1-to-1 — this is the fee for the whole class (one student).",
                     "zh": "1对1 —— 这就是整个班的学费（一个学生）。",
                     "ms": "1-ke-1 — ini yuran untuk seluruh kelas (seorang pelajar)."},
    "fee_rule_hint": {
        "en": "One fixed fee per month. 1-to-1: charged only when the student had a lesson. "
              "Small class: charged whenever the class ran that month, even if this student missed it.",
        "zh": "一个月一个固定学费。1对1：学生那个月有上课才算钱，没上课就不算。"
              "小班：只要那个月班有开课就算钱，就算这个学生没来也算。",
        "ms": "Satu yuran tetap sebulan. 1-ke-1: dikenakan hanya jika pelajar ada kelas. "
              "Kelas kecil: dikenakan jika kelas berjalan bulan itu.",
    },
    "pricing_fixed": {"en": "Fixed fee — same for every student",
                      "zh": "固定学费 — 所有学生一样", "ms": "Yuran tetap — sama untuk semua pelajar"},
    "pricing_per_student": {"en": "Set the fee per student when enrolling them",
                            "zh": "加学生时才逐个输入学费", "ms": "Tetapkan yuran setiap pelajar semasa daftar"},
    "fee_reminder": {"en": "Remember to set this student's fee for the class.",
                     "zh": "记得设置这个学生在这个班的学费。", "ms": "Ingat tetapkan yuran pelajar ini untuk kelas ini."},
    "stat_scheduled_lessons": {"en": "Lessons scheduled", "zh": "原本应上课数", "ms": "Kelas dijadual"},
    "stat_held_lessons": {"en": "Lessons held so far", "zh": "目前上课数", "ms": "Kelas dijalankan"},
    "stat_projected_fee": {"en": "Projected fee (full attendance)", "zh": "原本应收学费（全勤）", "ms": "Yuran diunjur"},
    "class_level": {"en": "Grade / Level", "zh": "年级", "ms": "Tingkatan"},
    "class_kind": {"en": "Type", "zh": "班级类型", "ms": "Jenis"},
    "kind_1v1": {"en": "1-to-1", "zh": "1对1", "ms": "1-ke-1"},
    "kind_1v3": {"en": "1-to-3", "zh": "1对3", "ms": "1-ke-3"},
    "kind_small": {"en": "Small class", "zh": "小班", "ms": "Kelas kecil"},
    "name_auto_placeholder": {"en": "auto: Subject + Grade + Type",
                              "zh": "自动：科目 + 年级 + 类型", "ms": "auto: Subjek + Tingkatan + Jenis"},
    "name_auto_hint": {"en": "Leave blank and the name is built from Subject + Grade + Type; the weekday is added if that name is already used. Clear it to re-generate.",
                       "zh": "留空的话，名字会用「科目 + 年级 + 类型」组成；如果重名，会自动加上星期几（马来文科用马来文星期）。清空就会重新生成。",
                       "ms": "Biar kosong — nama dibina dari Subjek + Tingkatan + Jenis; hari ditambah jika nama itu sudah digunakan."},
    "class_schedule": {"en": "Schedule", "zh": "上课时间", "ms": "Jadual"},
    "pick_days": {"en": "Tick the days this class runs each week.",
                  "zh": "勾选这个班每星期上课的日子。",
                  "ms": "Tandakan hari kelas ini berjalan setiap minggu."},
    "time_optional_hint": {"en": "Leave the time blank if it varies.",
                           "zh": "时间不固定的话，可以留空。",
                           "ms": "Biarkan masa kosong jika berubah-ubah."},
    "no_fixed_schedule": {"en": "No fixed schedule", "zh": "时间不固定", "ms": "Tiada jadual tetap"},
    "time_varies": {"en": "time varies", "zh": "时间不定", "ms": "masa berubah"},
    "enroll_student": {"en": "Enroll Student", "zh": "加入学生", "ms": "Daftar Pelajar"},
    "end_enrollment": {"en": "End Enrollment", "zh": "结束就读", "ms": "Tamat Pendaftaran"},
    "edit_dates": {"en": "Edit dates", "zh": "改日期", "ms": "Ubah tarikh"},
    "edit_dates_hint": {"en": "Leave the end date blank to keep the enrollment active; setting one marks it ended.",
                        "zh": "结束日期留空表示仍在就读；填了就算已结束。",
                        "ms": "Biarkan tarikh tamat kosong untuk kekal aktif; mengisinya menandakan tamat."},
    "start_date": {"en": "Start Date", "zh": "开始日期", "ms": "Tarikh Mula"},
    "end_date": {"en": "End Date", "zh": "结束日期", "ms": "Tarikh Tamat"},
    "current_class": {"en": "Current Class", "zh": "当前班级", "ms": "Kelas Semasa"},
    "previous_classes": {"en": "Previous Classes", "zh": "以前班级", "ms": "Kelas Terdahulu"},

    # ── fees ──
    "current_monthly_fee": {"en": "Current Monthly Fee", "zh": "目前月费", "ms": "Yuran Bulanan Semasa"},
    "fee_type": {"en": "Fee Type", "zh": "收费类型", "ms": "Jenis Yuran"},
    "effective_from": {"en": "Effective From", "zh": "生效日期", "ms": "Berkuat Kuasa Dari"},
    "special_discount": {"en": "Special Discount", "zh": "特别折扣", "ms": "Diskaun Khas"},
    "add_fee": {"en": "Add Fee", "zh": "新增收费", "ms": "Tambah Yuran"},
    "per_lesson_fee": {"en": "Per-lesson Fee", "zh": "每堂收费", "ms": "Yuran Setiap Kelas"},
    "own_rate": {"en": "own rate", "zh": "个人收费", "ms": "kadar sendiri"},
    "class_default": {"en": "class default", "zh": "班级默认", "ms": "lalai kelas"},

    # ── attendance ──
    "record_attendance": {"en": "Record Attendance", "zh": "记录出席", "ms": "Rekod Kehadiran"},
    "attendance_matrix": {"en": "Attendance Matrix", "zh": "出席表", "ms": "Matriks Kehadiran"},
    "backdated_note": {"en": "You can record or edit attendance for any past date.", "zh": "可以补写或修改任何过去日期的出席记录。", "ms": "Anda boleh merekod kehadiran untuk mana-mana tarikh lepas."},
    "add_session": {"en": "Add Session", "zh": "新增上课日", "ms": "Tambah Sesi"},
    "session_date": {"en": "Session Date", "zh": "上课日期", "ms": "Tarikh Sesi"},
    "total_sessions": {"en": "Total Sessions", "zh": "上课次数", "ms": "Jumlah Sesi"},
    "last_updated": {"en": "Last updated", "zh": "最后更新", "ms": "Dikemas kini"},
    "view_all_classes": {"en": "View all classes", "zh": "查看全部班级", "ms": "Lihat semua kelas"},
    "view_one_class": {"en": "View one class", "zh": "只看一个班", "ms": "Lihat satu kelas"},
    "makeup_of": {"en": "Making up which date", "zh": "补几号的课", "ms": "Ganti kelas tarikh mana"},

    # ── finance ──
    "monthly_overview": {"en": "Monthly Overview", "zh": "月度概况", "ms": "Ringkasan Bulanan"},
    "fin_tab_collect": {"en": "Collect", "zh": "收款", "ms": "Kutip"},
    "expected": {"en": "Expected", "zh": "应收", "ms": "Dijangka"},
    "paid": {"en": "Paid", "zh": "已缴", "ms": "Dibayar"},
    "st_pending": {"en": "Pending", "zh": "待缴", "ms": "Belum Bayar"},
    "st_partial": {"en": "Partially Paid", "zh": "部分缴付", "ms": "Bayaran Separa"},
    "st_paid": {"en": "Paid", "zh": "已缴清", "ms": "Selesai"},
    "st_overdue": {"en": "Overdue", "zh": "逾期", "ms": "Tertunggak"},
    "record_payment": {"en": "Record Payment", "zh": "记录收款", "ms": "Rekod Bayaran"},
    "amount": {"en": "Amount", "zh": "金额", "ms": "Jumlah"},
    "amount_paid": {"en": "Amount Paid (RM)", "zh": "已缴金额 (RM)", "ms": "Jumlah Dibayar (RM)"},
    "expected_override": {"en": "Expected (RM) — leave blank to keep the calculated amount",
                          "zh": "应收 (RM) — 留空就用系统算好的金额",
                          "ms": "Dijangka (RM) — biar kosong untuk kekalkan jumlah dikira"},
    "payment_date": {"en": "Payment Date", "zh": "收款日期", "ms": "Tarikh Bayaran"},
    "payment_method": {"en": "Payment Method", "zh": "付款方式", "ms": "Kaedah Bayaran"},
    "reference": {"en": "Reference", "zh": "参考编号", "ms": "Rujukan"},
    "pm_cash": {"en": "Cash", "zh": "现金", "ms": "Tunai"},
    "pm_bank": {"en": "Bank Transfer", "zh": "银行转账", "ms": "Pindahan Bank"},
    "pm_duitnow": {"en": "DuitNow", "zh": "DuitNow", "ms": "DuitNow"},
    "pm_other": {"en": "Other", "zh": "其他", "ms": "Lain-lain"},
    "receipt": {"en": "Receipt", "zh": "收据", "ms": "Resit"},
    "paste_or_choose": {"en": "Paste an image (Ctrl+V) or choose a file",
                        "zh": "贴上图片 (Ctrl+V) 或选择档案",
                        "ms": "Tampal imej (Ctrl+V) atau pilih fail"},
    "view_receipt": {"en": "View receipt", "zh": "查看收据", "ms": "Lihat resit"},
    "remove_receipt": {"en": "Remove this receipt", "zh": "移除这张收据", "ms": "Buang resit ini"},
    "estimated_month_income": {"en": "Estimated Income", "zh": "预测收入", "ms": "Pendapatan Anggaran"},
    "forecast_vs_actual": {"en": "Forecast vs Actual", "zh": "预测 vs 实际", "ms": "Ramalan vs Sebenar"},
    "generate_month": {"en": "Generate / Refresh", "zh": "生成 / 刷新", "ms": "Jana / Segar Semula"},
    "month_closed": {"en": "This month is closed. Reopen it to make changes.", "zh": "此月份已结算。要修改请先重新打开。", "ms": "Bulan ini telah ditutup."},
    "reopen_month": {"en": "Reopen Month", "zh": "重新打开", "ms": "Buka Semula"},
    "closing_desc": {"en": "Save a permanent financial snapshot for this month. Future edits will not change closed months.", "zh": "为这个月保存一份永久财务快照。之后修改资料不会影响已结算的月份。", "ms": "Simpan gambaran kewangan kekal untuk bulan ini."},

    # ── bills (parent-facing fee messages) ──
    "bills_hint": {"en": "One ready-to-send bilingual message per student, family and agent. Total = per-lesson fee × lessons held this month.",
                   "zh": "每个学生 / 家庭 / 代理一则可直接发送的中英账单。总额 = 每堂费 × 该月上课次数。",
                   "ms": "Satu mesej dwibahasa sedia hantar bagi setiap pelajar, keluarga dan ejen. Jumlah = yuran sekelas × kelas diadakan bulan ini."},
    "bills_individual": {"en": "Individual", "zh": "个人账单", "ms": "Individu"},
    "bills_family": {"en": "Family", "zh": "家庭账单", "ms": "Keluarga"},
    "bills_agent": {"en": "Agent", "zh": "代理账单", "ms": "Ejen"},
    "bill_copy": {"en": "Copy", "zh": "复制", "ms": "Salin"},
    "bill_copied": {"en": "Copied ✓", "zh": "已复制 ✓", "ms": "Disalin ✓"},
    "bill_none": {"en": "No bills for this month.", "zh": "这个月没有账单。", "ms": "Tiada bil untuk bulan ini."},
    "bills_unbilled": {"en": "Not billed this month", "zh": "这个月没生成账单", "ms": "Tiada bil bulan ini"},
    "bills_unbilled_hint": {"en": "These whole-class fees produced no bill. Fix the class setup or record its attendance.",
                            "zh": "这些整班收费没有生成账单。检查班级设置或补记出席。",
                            "ms": "Yuran kelas ini tiada bil. Semak tetapan kelas atau rekod kehadiran."},
    "bill_reason_no_billed_family": {"en": "no family to bill — put its students in a family, or pick one",
                                     "zh": "找不到要收费的家庭 —— 把学生归到一个家庭，或直接指定",
                                     "ms": "tiada keluarga untuk bil — letak pelajar dalam keluarga, atau pilih satu"},
    "bill_reason_no_agent": {"en": "no agent name", "zh": "没填代理名字", "ms": "tiada nama ejen"},
    "bill_reason_no_lessons": {"en": "no lessons held yet this month", "zh": "这个月还没上课", "ms": "belum ada kelas bulan ini"},
    "bill_reason_other": {"en": "check the class setup", "zh": "检查班级设置", "ms": "semak tetapan kelas"},
    "bills_join_family": {"en": "put in a family", "zh": "归到家庭", "ms": "letak dalam keluarga"},
    "class_bill": {"en": "Class Bill", "zh": "整班账单", "ms": "Bil Kelas"},
    "fee_breakdown": {"en": "Fee breakdown", "zh": "费用算法", "ms": "Pecahan yuran"},
    "per_class_fee": {"en": "Per class", "zh": "每堂", "ms": "Setiap kelas"},
    "billed_whole_class": {"en": "This class is billed as a whole — students have no individual fee.",
                           "zh": "这个班整班一起收费 —— 学生没有各自的学费。",
                           "ms": "Kelas ini dibilkan secara keseluruhan — pelajar tiada yuran individu."},

    # ── billing modes (per-lesson: fee = amount × lessons that ran) ──
    "bill_mode": {"en": "Billing mode", "zh": "收费方式", "ms": "Kaedah Bil"},
    "bill_mode_hint": {"en": "How this class's fee is calculated. Every fee = amount × lessons that ran.",
                       "zh": "这个班的学费怎么算。所有学费 = 金额 × 实际上课堂数。",
                       "ms": "Bagaimana yuran kelas ini dikira. Setiap yuran = jumlah × kelas yang berlangsung."},
    "bm_student_attend": {"en": "Per student — own attendance", "zh": "每位学生 · 按本人出席", "ms": "Setiap pelajar — kehadiran sendiri"},
    "bm_class_ran": {"en": "Per student — per class held", "zh": "每位学生 · 按班开课日", "ms": "Setiap pelajar — setiap kelas diadakan"},
    "bm_class_flat": {"en": "Flat class fee → family", "zh": "整班固定费 → 家庭", "ms": "Yuran kelas tetap → keluarga"},
    "bm_agent_headcount": {"en": "Base + headcount → agent", "zh": "基础费 + 人数 → 代理", "ms": "Asas + bilangan → ejen"},
    "lesson_fee": {"en": "Per-lesson fee (RM)", "zh": "每堂费 (RM)", "ms": "Yuran sekelas (RM)"},
    "lesson_fee_hint": {"en": "Charged per lesson the class ran.", "zh": "按班每上一堂收一次。", "ms": "Dikenakan setiap kelas berlangsung."},
    "class_flat_fee": {"en": "Flat fee per class (RM)", "zh": "整班每堂费 (RM)", "ms": "Yuran tetap sekelas (RM)"},
    "base_fee": {"en": "Base fee", "zh": "基础费", "ms": "Yuran asas"},
    "base_heads": {"en": "Base fee covers (students)", "zh": "基础费包含几位学生", "ms": "Yuran asas meliputi (pelajar)"},
    "per_head": {"en": "Per extra student", "zh": "每多一位学生", "ms": "Setiap pelajar tambahan"},
    "agent": {"en": "Agent", "zh": "代理", "ms": "Ejen"},
    "agent_phone": {"en": "Agent phone", "zh": "代理电话", "ms": "Telefon ejen"},
    "billed_family": {"en": "Billed family", "zh": "收费家庭", "ms": "Keluarga dibilkan"},
    "billed_family_hint": {"en": "Leave blank to auto-use the family of the students in this class.",
                           "zh": "留空的话，自动收给这个班学生所在的家庭。",
                           "ms": "Biarkan kosong untuk guna keluarga pelajar dalam kelas ini secara automatik."},
    "fee_model": {"en": "Fee type", "zh": "计费方式", "ms": "Jenis yuran"},
    "fee_model_monthly": {"en": "Monthly (legacy)", "zh": "月费（旧）", "ms": "Bulanan (lama)"},
    "fee_model_per_lesson": {"en": "Per lesson", "zh": "每堂", "ms": "Sekelas"},

    # ── results ──
    "add_result": {"en": "Add Result", "zh": "新增成绩", "ms": "Tambah Keputusan"},
    "exam_name": {"en": "Exam", "zh": "考试", "ms": "Peperiksaan"},
    "exam_date": {"en": "Date", "zh": "日期", "ms": "Tarikh"},
    "score": {"en": "Score", "zh": "分数", "ms": "Markah"},
    "grade": {"en": "Grade", "zh": "等级", "ms": "Gred"},
    "out_of": {"en": "out of", "zh": "满分", "ms": "daripada"},
    "trend": {"en": "Trend", "zh": "趋势", "ms": "Trend"},

    # ── reports ──
    "student_reports": {"en": "Student Reports", "zh": "学生报告", "ms": "Laporan Pelajar"},
    "attendance_reports": {"en": "Attendance Reports", "zh": "出席报告", "ms": "Laporan Kehadiran"},
    "finance_reports": {"en": "Finance Reports", "zh": "财务报告", "ms": "Laporan Kewangan"},
    "monthly_revenue": {"en": "Monthly Revenue", "zh": "月度收入", "ms": "Hasil Bulanan"},
    "class_revenue": {"en": "Class Revenue", "zh": "班级收入", "ms": "Hasil Kelas"},
    "new_students": {"en": "New Students", "zh": "新生", "ms": "Pelajar Baharu"},
    "students_left": {"en": "Students Left", "zh": "离开学生", "ms": "Pelajar Berhenti"},

    # ── settings ──
    "language": {"en": "Language", "zh": "语言", "ms": "Bahasa"},
    "currency": {"en": "Currency", "zh": "货币", "ms": "Mata Wang"},
    "charge_absence": {"en": "Charge per-lesson fee when student is absent", "zh": "按堂计费时，学生缺席也照收费", "ms": "Kenakan yuran walaupun pelajar tidak hadir"},
    "result_subjects": {"en": "Result Subjects (comma separated)", "zh": "成绩科目（用逗号分隔）", "ms": "Subjek Keputusan (dipisahkan koma)"},
    "payment_info": {"en": "Payment Info (bank / DuitNow details for messages)", "zh": "收款资料（银行 / DuitNow，用于讯息）", "ms": "Maklumat Bayaran"},
    "change_password": {"en": "Change Password", "zh": "更改密码", "ms": "Tukar Kata Laluan"},
    "new_password": {"en": "New Password", "zh": "新密码", "ms": "Kata Laluan Baharu"},
}

WEEKDAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "zh": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
    "ms": ["Isnin", "Selasa", "Rabu", "Khamis", "Jumaat", "Sabtu", "Ahad"],
}
WEEKDAYS_SHORT = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "zh": ["一", "二", "三", "四", "五", "六", "日"],
    "ms": ["Isn", "Sel", "Rab", "Kha", "Jum", "Sab", "Ahd"],
}
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
    "zh": ["一月", "二月", "三月", "四月", "五月", "六月", "七月", "八月",
           "九月", "十月", "十一月", "十二月"],
    "ms": ["Januari", "Februari", "Mac", "April", "Mei", "Jun", "Julai",
           "Ogos", "September", "Oktober", "November", "Disember"],
}


def current_lang():
    return getattr(g, "lang", "en")


def t(key):
    entry = STRINGS.get(key)
    if not entry:
        return key
    lang = current_lang()
    return entry.get(lang) or entry.get("en") or key


def weekday_name(idx):
    if idx is None or idx == "":
        return "—"
    try:
        return WEEKDAYS[current_lang()][int(idx)]
    except (ValueError, IndexError, KeyError):
        return "—"


def weekday_short(idx):
    if idx is None or idx == "":
        return "—"
    try:
        return WEEKDAYS_SHORT[current_lang()][int(idx)]
    except (ValueError, IndexError, KeyError):
        return "—"


def month_name(m):
    try:
        return MONTHS[current_lang()][int(m) - 1]
    except (ValueError, IndexError, KeyError):
        return str(m)


def month_label(ym):
    """'2026-08' -> 'August 2026' / '2026年8月' / 'Ogos 2026'."""
    try:
        y, m = ym.split("-")
        if current_lang() == "zh":
            return f"{y}年{int(m)}月"
        return f"{month_name(m)} {y}"
    except (ValueError, AttributeError):
        return ym
