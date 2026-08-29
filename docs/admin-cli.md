# Provisionamento de administrador (CLI)

Estes são os únicos comandos suportados para criar ou redefinir a senha de um
administrador interno do HUB. O script `get_admin.py` foi removido por
representar um risco de segurança (senha padrão, impressa no console, e
redefinição silenciosa do primeiro administrador encontrado).

## Ambiente necessário

Os comandos usam a factory da aplicação (`create_app()`) e, portanto, seguem
as mesmas regras de ambiente já documentadas em `.env.example`:

- `FLASK_ENV` seleciona o ambiente (`development`, `testing`, `staging`,
  `production`);
- em `staging`/`production`, `SECRET_KEY` e `DATABASE_URL` são obrigatórias e
  validadas antes de qualquer operação;
- os comandos exigem contexto de aplicação Flask (`flask <comando>`), com
  `FLASK_APP` apontando para a factory do projeto.

## Criar um administrador

```
flask create-admin --name "Nome Completo" --email admin@dominio.exemplo
```

- `--name` e `--email` são opções explícitas.
- A senha **nunca** é passada por argumento de linha de comando — ela é
  solicitada de forma interativa, com **entrada oculta** (os caracteres
  digitados não aparecem na tela) e **confirmação obrigatória** (digitada
  duas vezes).
- Se o e-mail já pertencer a um usuário existente, o comando recusa a
  operação e **não altera nada** — use `reset-admin-password` para trocar a
  senha de um administrador já existente.

## Redefinir a senha de um administrador existente

```
flask reset-admin-password --email admin@dominio.exemplo
```

- Identifica o administrador **exclusivamente** pelo e-mail informado —
  nunca escolhe "o primeiro administrador encontrado".
- Exige confirmação explícita da ação antes de prosseguir (responda `y` ao
  prompt, ou passe `--yes` para automação controlada).
- A nova senha também é solicitada de forma interativa, com entrada oculta e
  confirmação — nunca por argumento de linha de comando.
- Se nenhum administrador interno for encontrado com o e-mail informado, o
  comando recusa a operação e não cria um novo usuário.

## Observações de segurança

- Nenhum dos dois comandos imprime, registra em log ou inclui a senha em
  mensagens de erro.
- Nenhuma senha padrão existe; a senha é sempre validada (não vazia, com
  comprimento mínimo) antes de qualquer gravação no banco.
- A senha é armazenada exclusivamente como hash, via `User.set_password()`.
