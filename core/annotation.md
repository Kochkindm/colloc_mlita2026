В файле classes.py:
1. Общий контракт классов:

   Program
   Formula
   VC
   Verdict
   VCResult

2. Вспомогательные структуры:

   Expr
   Command
   Variable
   Constant
   BinaryOp
   UnaryOp
   Call
   Attribute
   Comparison
   BoolOp
   Not
   BoolConstant
   Assign
   If
   While
   Assert
   Assume
   Return

3. Интерфейсы ролей:

   parse(source) -> Program

   wp(command, postcondition) -> Formula

   generate(program) -> list[VC]

   solve(vc) -> VCResult




Роль 2 — Парсер языка и аннотаций
Использует: Program, Command, Expr, Formula
Что делает: Python-код → Program
1. Берёт исходный Python-код.

2. Преобразует Python-код в класс Program.

3. Разбирает:
   - переменные
   - выражения
   - if
   - while
   - assignment
   - return
   - requires
   - ensures
   - invariant

4. Передаёт Program роли 4.



Роль 3 — Семантика и weakest precondition
Использует: Command, Expr, Formula
Что делает: wp(command, postcondition) → Formula
1. Получает Command и Formula.

2. Реализует weakest precondition.

3. Преобразует:
   - Command + Formula → Formula

4. Реализует правила для:
   - assignment
   - if
   - последовательности
   - assert
   - assume
   - while

5. Передаёт Formula роли 4.


Роль 4 — Генератор условий корректности (VC)
Использует: Program, Formula, VC
Что делает: Program → list[VC]
1. Получает Program.

2. Использует WP из роли 3.

3. Проходит по программе.

4. Генерирует verification conditions.

5. Преобразует:
   - Program → list[VC]

6. Передаёт VC роли 5 и 6.


Роль 5 — Z3 адаптер
Использует: VC, Formula, Verdict, VCResult
Что делает: VC → VCResult
1. Получает VC.

2. Преобразует Formula в формат Z3.

3. Запускает Z3.

4. Получает результат:
   - Proved
   - Failed(model)
   - Unknown(reason)

5. Преобразует:
   - VC → VCResult

6. Передаёт результат роли 8.


Роль 6 — Собственный пропозициональный решатель
Использует: VC, Formula, Verdict
Что делает: VC → Verdict
1. Получает VC.

2. Проверяет Boolean-формулу своим решателем.

3. Реализует DPLL или resolution.

4. Возвращает:
   - Proved
   - Failed(model)
   - Unknown(reason)

5. Преобразует:
   - VC → Verdict

6. Сравнивает результаты с Z3.


Роль 7 — Набор верифицируемых программ и эталонных тестов
Использует: ничего напрямую
Что делает: отдаёт исходный Python-код
1. Собирает реальные функции из предыдущего проекта.
БРАТЬ ТОЛЬКО ТЕ ФУНКЦИИ, В ТЕЛЕ КОТОРЫХ НЕТ ДРУГИХ ФУНКЦИЙ ИЗ ЭТОГО ПРОЕКТА
ИНАЧЕ ВОЗНИКНУТ ЛИШНИЕ ПРОБЛЕМЫ
2. Выбирает 3 основные задачи (ПРИМЕР!!! НЕ ОБЯЗАТЕЛЬНО КОНКРЕТНО ЭТИ):
   - gcd
   - derivative
   - simplify

3. Для каждой делает:
   - корректную версию
   - намеренно сломанную версию

4. Для каждой функции определяет:
   - requires
   - ensures
   - invariant, если есть цикл

5. Передаёт команде исходные .py-файлы.
Например: programs/
├── gcd/
│   ├── correct.py
│   └── broken.py
├── derivative/
│   ├── correct.py
│   └── broken.py
└── simplify/
    ├── correct.py
    └── broken.py


Роль 8 — UI
Использует: Program, VCResult, Verdict
Что делает: показывает результат пользователю
1. Получает результаты проверки.

2. Показывает:
   - список VC
   - статус каждого VC
   - Proved / Failed / Unknown
   - counterexample при Failed

3. Показывает строку исходного кода, если она известна.


Роль 9 — Testing
Использует: все публичные структуры
Что делает: проверяет роли между собой
1. Тестирует каждую роль отдельно.

2. Проверяет:
   - Python → Program
   - Command + Formula → Formula
   - Program → VC
   - VC → Verdict

3. Проверяет корректные программы.

4. Проверяет сломанные программы.

5. Сравнивает Z3 и собственный solver.

6. Проверяет полный pipeline от Python-кода до Verdict.
