# Política Obrigatória de Issues, Branches, Pull Requests e Deploys

## Gestão de Tarefas e Rastreabilidade via GitHub

Toda alteração realizada no projeto deverá possuir **rastreabilidade obrigatória no GitHub**.

Esta regra se aplica a **qualquer agente, desenvolvedor, automação ou modelo de IA**, independentemente da ferramenta, modelo ou ambiente utilizado para executar a tarefa.

Nenhuma alteração deverá ser implementada diretamente na branch principal sem estar vinculada a uma Issue e sem passar pelo fluxo de Pull Request definido neste documento.

---

## 1. Toda Tarefa Deve Possuir uma Issue

Antes de iniciar qualquer alteração no código, o agente deverá verificar se existe uma Issue correspondente à tarefa.

A regra se aplica, inclusive, a:

* Correções de bugs;
* Correções de segurança;
* Melhorias;
* Novas funcionalidades;
* Alterações de interface;
* Alterações de comportamento;
* Alterações de configuração;
* Alterações de infraestrutura;
* Alterações de testes;
* Alterações de documentação que impactem o funcionamento ou as regras do projeto;
* Atualizações necessárias para correção ou evolução do sistema.

### Regra obrigatória

> **Uma tarefa = uma Issue = uma unidade de rastreabilidade.**

Caso já exista uma Issue claramente correspondente à tarefa, o agente deverá utilizá-la em vez de criar uma Issue duplicada.

Caso não exista Issue correspondente, o agente deverá criar uma antes de iniciar a implementação.

O agente **não deverá iniciar a implementação de uma nova tarefa sem que exista uma Issue correspondente**, salvo quando o próprio fluxo do repositório estabelecer formalmente uma exceção.

---

# 2. Conteúdo Mínimo da Issue

A Issue deverá descrever claramente o trabalho que será realizado.

Sempre que aplicável, deverá conter:

* **Título objetivo**;
* **Tipo da tarefa**;
* Contexto;
* Problema ou necessidade;
* Objetivo;
* Escopo;
* Critérios de aceitação;
* Restrições técnicas relevantes;
* Impactos esperados;
* Dependências;
* Observações necessárias para implementação.

O título deverá permitir identificar rapidamente a natureza da alteração.

Preferencialmente, utilizar uma convenção semelhante a:

```text
[BUG] Corrigir validação de URL do HUB
[FEATURE] Criar catálogo de sistemas no HUB
[IMPROVEMENT] Melhorar navegação da página SaaS
[SECURITY] Impedir redirecionamento externo controlado pelo usuário
[DOCS] Atualizar política de contribuição
```

Caso o repositório já possua uma convenção própria, ela deverá prevalecer.

---

# 3. Uma Issue Deve Ser a Unidade de Rastreabilidade

O agente deverá manter a relação entre:

```text
Issue
   ↓
Branch
   ↓
Commits
   ↓
Pull Request
   ↓
CI / Testes
   ↓
Review
   ↓
Merge
   ↓
Deploy
```

Essa relação deverá ser preservada durante todo o ciclo de vida da tarefa.

Não deverão ser criadas alterações cuja origem ou finalidade não possa ser relacionada a uma Issue.

---

# 4. Branch Obrigatória para Implementação

A implementação deverá ocorrer em uma branch própria vinculada à Issue.

O agente deverá verificar primeiro as convenções de nomenclatura já existentes no repositório.

Na ausência de convenção específica, utilizar um padrão equivalente a:

```text
feature/<issue-number>-descricao
bugfix/<issue-number>-descricao
improvement/<issue-number>-descricao
security/<issue-number>-descricao
docs/<issue-number>-descricao
```

Exemplos:

```text
feature/42-hub-sistemas
bugfix/57-validacao-hub-url
improvement/63-pagina-saas
```

A branch principal (`main`, `master` ou equivalente) deverá ser tratada como branch protegida.

O agente não deverá utilizar a branch principal como ambiente de desenvolvimento da tarefa.

---

# 5. Implementação Restrita ao Escopo da Issue

O agente deverá implementar somente o escopo definido na Issue e nas instruções relacionadas à tarefa.

Não deverão ser introduzidas, incidentalmente:

* refatorações não solicitadas;
* melhorias arquiteturais não relacionadas;
* atualizações de dependências sem necessidade;
* alterações de estilo sem relação com a tarefa;
* alterações em funcionalidades não relacionadas;
* reorganizações de código sem justificativa;
* correções oportunistas não relacionadas.

Caso durante a implementação seja identificada outra necessidade, o agente deverá avaliar se ela é indispensável para concluir a tarefa.

Se não for indispensável, deverá ser criada uma **nova Issue** para tratamento separado.

---

# 6. Commits

Os commits deverão ser pequenos, intencionais e relacionados à Issue.

Sempre que possível, utilizar mensagens que permitam compreender claramente o objetivo da alteração.

Exemplos:

```text
feat: adiciona catálogo inicial de sistemas
fix: valida HUB_URL em produção
test: adiciona cobertura para open redirect
docs: atualiza política de desenvolvimento
```

Quando compatível com a convenção adotada pelo projeto, o commit deverá referenciar a Issue.

Exemplo:

```text
feat: adiciona catálogo de sistemas (#42)
```

Não deverão ser realizados commits contendo alterações não relacionadas à Issue.

---

# 7. Pull Request Obrigatório

Toda alteração destinada à branch principal deverá ser entregue por meio de **Pull Request**.

O agente não deverá considerar uma tarefa concluída simplesmente porque o código foi alterado localmente ou porque os testes locais foram executados.

O fluxo esperado será:

```text
Issue
  ↓
Branch
  ↓
Implementação
  ↓
Testes
  ↓
Pull Request
  ↓
CI
  ↓
Review
  ↓
Merge
  ↓
Deploy
```

O Pull Request deverá ser criado a partir da branch da tarefa para a branch principal definida pelo projeto.

---

# 8. Issue Obrigatoriamente Mencionada no Pull Request

Todo Pull Request deverá mencionar explicitamente a Issue relacionada.

A descrição do PR deverá conter a referência correspondente à Issue.

Quando a alteração concluir a Issue integralmente, utilizar, quando compatível com a política do repositório:

```text
Closes #42
```

Também poderão ser utilizadas outras palavras-chave reconhecidas pelo GitHub, conforme o fluxo adotado pelo projeto:

```text
Fixes #42
Resolves #42
```

Quando a Issue não deva ser encerrada automaticamente pelo merge, utilizar uma referência simples:

```text
Related to #42
```

ou:

```text
Refs #42
```

A convenção deverá ser escolhida de acordo com o objetivo do PR.

### Regra fundamental

> **Nenhum Pull Request deverá ser considerado completo sem vínculo explícito com sua Issue correspondente.**

O vínculo deverá estar presente na descrição do PR, e não apenas implicitamente no nome da branch ou nos commits.

---

# 9. Estrutura Mínima do Pull Request

Sempre que aplicável, o PR deverá conter:

```markdown
## Issue
Closes #42

## Objetivo
Descrição objetiva do que foi alterado.

## Alterações realizadas
- Alteração 1
- Alteração 2
- Alteração 3

## Testes executados
- `python -m pytest`
- `python -m ruff check .`

## Verificações
- [ ] Testes automatizados
- [ ] Lint
- [ ] Build
- [ ] Verificação manual
- [ ] CI aprovado

## Impactos
Descrição de impactos relevantes.

## Observações
Limitações, pendências ou informações adicionais.
```

O formato deverá ser adaptado ao template de Pull Request existente no repositório.

Se o projeto já possuir um template oficial, **não criar outro nem substituí-lo sem necessidade**.

---

# 10. Testes e CI Antes do Merge

Antes do merge, o agente deverá executar os testes e verificações exigidos pelo repositório.

Isso poderá incluir:

* testes unitários;
* testes de integração;
* testes end-to-end;
* lint;
* type checking;
* análise estática;
* build;
* verificações de segurança;
* cobertura;
* validações específicas do projeto.

O agente não deverá declarar que os testes foram aprovados sem executá-los efetivamente.

Falhas deverão ser registradas de forma transparente.

O agente não deverá:

* mascarar falhas;
* remover testes para obter aprovação;
* reduzir cobertura artificialmente;
* ignorar erros sem justificativa;
* desabilitar verificações de CI para permitir merge;
* declarar sucesso sem evidência.

---

# 11. Deploy Controlado por Pull Request

O **Pull Request será a unidade de controle e rastreabilidade para Deploys**, sempre que a infraestrutura do projeto permitir.

O agente deverá respeitar o fluxo de CI/CD existente.

O processo esperado será:

```text
Issue criada
     ↓
Branch criada
     ↓
Implementação
     ↓
Testes locais
     ↓
Pull Request
     ↓
CI
     ↓
Review / Aprovação
     ↓
Merge
     ↓
Deploy
```

O agente não deverá realizar deploy manual fora do processo oficial do projeto, salvo quando isso estiver explicitamente previsto pela infraestrutura e pelas políticas do repositório.

Caso o projeto utilize deploy automático após merge, o agente deverá tratar o merge como o gatilho de progressão para o ambiente seguinte.

Caso o projeto utilize aprovação manual para deploy, essa aprovação deverá ser respeitada.

---

# 12. Não Simular Etapas

É expressamente proibido declarar como realizadas atividades que não tenham sido efetivamente executadas.

O agente não deverá afirmar que:

* uma Issue foi criada se não foi criada;
* um branch foi criado se não foi criado;
* um commit foi realizado se não foi realizado;
* um PR foi aberto se não foi aberto;
* testes foram executados se não foram executados;
* CI foi aprovado se não foi executado ou aprovado;
* code review foi realizado se não ocorreu;
* merge foi realizado se não ocorreu;
* deploy foi realizado se não ocorreu;
* aplicação foi publicada se não houver evidência disso.

Quando uma etapa não puder ser executada por limitação de acesso, ferramenta, permissão ou infraestrutura, o agente deverá informar explicitamente a limitação.

---

# 13. Agentes de Qualquer Modelo Devem Respeitar Esta Política

Estas regras são **normativas do projeto** e não dependem do modelo de IA utilizado.

Todo agente que trabalhar neste repositório deverá considerar este documento como instrução obrigatória, incluindo, mas não se limitando a:

* agentes de IA;
* modelos OpenAI;
* Claude;
* Gemini;
* agentes executados pelo Antigravity;
* agentes executados pelo Codex;
* GitHub Copilot;
* agentes autônomos;
* ferramentas de automação;
* desenvolvedores humanos.

O agente deverá ler e respeitar este arquivo antes de iniciar qualquer tarefa.

Caso exista conflito entre instruções locais e instruções globais do repositório, deverão ser aplicadas as regras de precedência estabelecidas pelo próprio projeto e pela documentação normativa existente.

---

# 14. Persistência das Regras

Estas instruções deverão permanecer documentadas no repositório, preferencialmente em:

```text
AGENTS.md
```

Caso o projeto utilize outro arquivo normativo para instruções de agentes, a regra deverá ser registrada também nesse arquivo, conforme a arquitetura documental existente.

O objetivo é garantir que **qualquer novo agente, independentemente do modelo ou ferramenta utilizada, encontre essas regras diretamente no repositório**.

Não depender exclusivamente de instruções fornecidas em uma conversa, prompt temporário ou contexto externo.

---

# 15. Regra de Não Bypass

Nenhum agente deverá contornar este processo simplesmente porque a alteração é pequena.

Exemplos de tarefas que ainda deverão possuir rastreabilidade:

```text
Correção de typo funcional
Correção de bug
Alteração de CSS
Alteração de template
Alteração de rota
Alteração de configuração
Alteração de variável de ambiente
Nova funcionalidade
Alteração de segurança
Alteração de testes
Alteração de documentação normativa
```

A única exceção será quando o próprio repositório estabelecer formalmente uma política específica para alterações menores.

---

# 16. Checklist Obrigatório Antes da Entrega

Antes de considerar qualquer tarefa concluída, o agente deverá verificar:

* [ ] Existe uma Issue correspondente à tarefa.
* [ ] A Issue descreve adequadamente o objetivo.
* [ ] A branch está vinculada à Issue.
* [ ] A implementação está restrita ao escopo da Issue.
* [ ] Os commits estão relacionados à tarefa.
* [ ] Os testes necessários foram executados.
* [ ] O lint/verificações obrigatórias foram executados.
* [ ] O Pull Request foi criado.
* [ ] O Pull Request menciona explicitamente a Issue.
* [ ] A descrição do PR contém as evidências reais da implementação.
* [ ] O CI foi executado quando aplicável.
* [ ] As aprovações obrigatórias foram obtidas quando aplicável.
* [ ] O merge ocorreu somente conforme as políticas do projeto.
* [ ] O deploy ocorreu somente conforme o fluxo oficial.
* [ ] Nenhuma etapa foi declarada como concluída sem ter sido efetivamente realizada.

---

# Regra Fundamental

> **Nenhuma alteração deverá ser considerada concluída apenas porque o código funciona localmente.**

A conclusão da tarefa deverá ser determinada pelo ciclo completo de engenharia adotado pelo projeto:

```text
┌──────────────┐
│    ISSUE     │
└──────┬───────┘
       ↓
┌──────────────┐
│    BRANCH    │
└──────┬───────┘
       ↓
┌──────────────┐
│ IMPLEMENTAÇÃO│
└──────┬───────┘
       ↓
┌──────────────┐
│    TESTES    │
└──────┬───────┘
       ↓
┌──────────────┐
│      PR      │──────→ Issue vinculada
└──────┬───────┘
       ↓
┌──────────────┐
│     CI/CD    │
└──────┬───────┘
       ↓
┌──────────────┐
│    REVIEW    │
└──────┬───────┘
       ↓
┌──────────────┐
│     MERGE    │
└──────┬───────┘
       ↓
┌──────────────┐
│    DEPLOY    │
└──────────────┘
```

**Esse fluxo deverá ser considerado parte integrante da engenharia do projeto e deverá ser respeitado por qualquer agente que modificar o repositório.**

<br>

# Diretriz Obrigatória — Motion Design, Loading States e Princípios de Interface

A implementação de **toda interface do sistema** deverá seguir os princípios de Motion Design definidos na Skill **Motion Principles**, disponível no repositório:

`github.com/kylezantos/design-principles`

Antes de implementar ou alterar qualquer interface, o agente deverá **consultar e aplicar efetivamente as orientações dessa Skill**, respeitando seus princípios, padrões, recomendações e restrições.

> **Regra fundamental:** Motion Design não deverá ser tratado como um detalhe cosmético ou aplicado apenas à página inicial. Ele deverá fazer parte da experiência funcional de toda a aplicação.

---

## 1. Aplicação Global

Os princípios da Skill deverão ser considerados em:

* páginas;
* componentes;
* cards;
* botões;
* formulários;
* tabelas;
* menus;
* modais;
* drawers;
* dropdowns;
* notificações;
* alertas;
* tooltips;
* navegação;
* filtros;
* pesquisas;
* carregamento de dados;
* paginação;
* operações assíncronas;
* transições entre estados;
* estados vazios;
* estados de erro;
* estados de sucesso;
* operações de criação, edição e exclusão;
* carregamento inicial;
* atualizações parciais de conteúdo.

Sempre que houver mudança perceptível de estado, o agente deverá avaliar se uma transição, animação, feedback visual ou indicador de progresso melhora a compreensão da ação.

---

# 2. Loading States e Skeleton

Toda interface que depender de carregamento assíncrono deverá possuir um estado de carregamento adequado.

Quando houver conteúdo estruturado que possa ser antecipado visualmente, deverá ser utilizado **Skeleton Loading**, preservando aproximadamente a estrutura e as dimensões do conteúdo que será carregado.

O Skeleton deverá:

* aparecer imediatamente quando necessário;
* evitar mudanças bruscas de layout;
* preservar o espaço reservado para o conteúdo;
* utilizar animação suave e discreta;
* não provocar *layout shift* desnecessário;
* ser substituído pelo conteúdo real de forma suave;
* respeitar `prefers-reduced-motion`.

Não utilizar simplesmente um spinner genérico quando um Skeleton contextualizado proporcionar uma experiência melhor.

### Exemplos

Cards:

```text
┌──────────────────────────┐
│ ░░░░░░░░░░░░░░░░░░░░░░░░ │
│                          │
│ ░░░░░░░░░░░░             │
│ ░░░░░░░░░░░░░░░░░        │
│                          │
│ ░░░░░░░░░░░░░░           │
└──────────────────────────┘
```

Tabelas:

* preservar quantidade aproximada de linhas;
* preservar largura das colunas;
* evitar que a tabela “salte” quando os dados forem carregados.

Listas:

* preservar altura aproximada dos itens;
* evitar deslocamento abrupto dos elementos.

---

# 3. Lazy Loading

Sempre que tecnicamente aplicável, utilizar **Lazy Loading** para conteúdos que não precisem ser carregados imediatamente.

Avaliar, conforme o caso:

* imagens;
* componentes pesados;
* conteúdo abaixo da dobra;
* módulos;
* dados secundários;
* recursos externos;
* componentes que dependam de processamento significativo.

O Lazy Loading deverá ser acompanhado de feedback visual adequado quando houver tempo perceptível de carregamento.

Não utilizar Lazy Loading indiscriminadamente quando isso prejudicar:

* SEO;
* acessibilidade;
* desempenho percebido;
* conteúdo crítico;
* *Largest Contentful Paint*;
* navegação;
* experiência do usuário.

---

# 4. Animações de Entrada e Saída

Componentes que sejam adicionados ou removidos da interface deverão possuir, quando apropriado, uma transição visual suave.

Aplicar Motion Design especialmente em:

* modais;
* drawers;
* menus;
* dropdowns;
* notificações;
* cards dinâmicos;
* mensagens;
* filtros;
* elementos condicionais;
* páginas ou seções carregadas dinâmicos.

As animações deverão comunicar **origem, destino, hierarquia e mudança de estado**, evitando movimentos meramente decorativos.

Priorizar, quando adequado:

```css
opacity
transform
```

Evitar animações que provoquem alterações desnecessárias de:

```text
layout
width
height
top
left
margin
padding
```

quando uma alternativa baseada em `transform` ou `opacity` puder produzir o mesmo resultado.

---

# 5. Estados de Interação

Os elementos interativos deverão fornecer feedback visual claro para seus estados relevantes.

Considerar, conforme aplicável:

```text
default
hover
focus
active
pressed
disabled
loading
success
error
```

Botões que executem operações assíncronas deverão possuir estado de carregamento.

Exemplo conceitual:

```text
[ Salvar ]
      ↓
[ ⟳ Salvando... ]
      ↓
[ ✓ Salvo ]
```

O feedback deverá deixar claro:

1. que a ação foi recebida;
2. que está sendo processada;
3. quando foi concluída;
4. quando ocorreu erro.

Evitar situações em que o usuário precise clicar repetidamente porque não consegue identificar se a operação está em andamento.

---

# 6. Progress Feedback

Operações demoradas deverão fornecer indicação de progresso sempre que tecnicamente possível.

Quando o progresso percentual for conhecido:

```text
Processando documentos...

██████████████░░░░░░ 72%
```

Quando não for possível determinar o percentual:

* utilizar indicador de progresso indeterminado;
* Skeleton;
* feedback textual;
* ou outro padrão apropriado definido pela Skill.

O usuário deverá receber feedback proporcional à duração da operação.

---

# 7. Transição entre Estados

A transição entre:

```text
Loading
   ↓
Content
```

```text
Loading
   ↓
Error
```

```text
Loading
   ↓
Empty State
```

```text
Action
   ↓
Success
```

deverá ser visualmente coerente.

Evitar substituições abruptas que façam a interface “piscar”, “pular” ou deslocar o conteúdo inesperadamente.

Sempre que possível, preservar:

* dimensões;
* posição;
* hierarquia;
* contexto;
* continuidade visual.

---

# 8. Navegação e Transições de Página

A navegação deverá possuir feedback visual apropriado.

Quando houver transições de página, carregamento de rota ou operações assíncronas relevantes, avaliar a utilização de:

* indicadores de carregamento;
* progress bar;
* transições suaves;
* Skeleton;
* preservação do conteúdo existente até a chegada do novo conteúdo.

Não utilizar animações de transição simplesmente porque são visualmente atraentes. A animação deverá ter função comunicativa.

---

# 9. Acessibilidade e `prefers-reduced-motion`

**Nenhuma implementação de Motion Design poderá comprometer acessibilidade.**

É obrigatório respeitar:

```css
@media (prefers-reduced-motion: reduce) {
    /* reduzir ou remover movimentos não essenciais */
}
```

Usuários que tenham solicitado redução de movimento deverão receber uma experiência equivalente em funcionalidade, porém com:

* menos movimento;
* transições reduzidas;
* ausência de efeitos excessivos;
* eliminação de animações não essenciais.

Não esconder informações ou funcionalidades apenas porque o movimento foi reduzido.

---

# 10. Performance

Motion Design deverá ser implementado considerando desempenho.

Priorizar animações que possam ser executadas de forma eficiente pelo navegador.

Evitar:

* animações pesadas;
* loops desnecessários;
* JavaScript executado continuamente apenas para efeitos visuais;
* múltiplas animações concorrentes sem necessidade;
* efeitos que provoquem *layout thrashing*;
* animações que prejudiquem dispositivos móveis.

O agente deverá considerar especialmente:

```text
Performance
Accessibility
Responsiveness
Perceived Performance
CPU/GPU usage
Battery consumption
```

Motion Design não poderá comprometer a performance geral da aplicação.

---

# 11. Evitar Animação Excessiva

A aplicação **não deverá transformar todo elemento da interface em um elemento animado**.

A presença de Motion Design deverá ser funcional e intencional.

Não utilizar animação apenas para:

* chamar atenção desnecessariamente;
* criar “efeito visual” sem função;
* aumentar complexidade;
* tornar a interface mais chamativa;
* aplicar o mesmo efeito indiscriminadamente em todos os componentes.

A animação deverá responder a uma pergunta objetiva:

> **O movimento ajuda o usuário a compreender o que aconteceu, onde algo apareceu, para onde algo foi ou qual é o estado atual da operação?**

Se a resposta for não, a animação deverá ser reconsiderada.

---

# 12. Checklist Obrigatório por Interface

Antes de considerar uma interface concluída, o agente deverá verificar:

### Loading

* [ ] Existe estado de carregamento quando necessário.
* [ ] Skeleton foi utilizado quando apropriado.
* [ ] O carregamento não provoca *layout shift* desnecessário.
* [ ] O conteúdo carregado substitui o Skeleton de forma suave.

### Lazy Loading

* [ ] Conteúdos não críticos foram avaliados para Lazy Loading.
* [ ] Lazy Loading não prejudica SEO, acessibilidade ou performance.
* [ ] Recursos pesados são carregados somente quando necessário.

### Motion

* [ ] Entrada de elementos foi avaliada.
* [ ] Saída de elementos foi avaliada.
* [ ] Transições entre estados foram avaliadas.
* [ ] Estados de interação possuem feedback visual.
* [ ] Operações assíncronas possuem feedback de carregamento.
* [ ] Operações demoradas possuem progresso quando possível.
* [ ] Animações priorizam `transform` e `opacity` quando aplicável.
* [ ] Não existem animações desnecessárias.

### Acessibilidade

* [ ] `prefers-reduced-motion` foi considerado.
* [ ] Navegação por teclado continua funcional.
* [ ] Focus states permanecem visíveis.
* [ ] Motion Design não é requisito para compreender ou utilizar a interface.

### Performance

* [ ] Não foram introduzidas animações pesadas sem necessidade.
* [ ] Não há loops de animação desnecessários.
* [ ] Não há alterações de layout evitáveis.
* [ ] A experiência permanece adequada em dispositivos móveis.

---

# 13. Regra para Novas Funcionalidades

Toda nova funcionalidade deverá ser projetada considerando, desde o início, seus estados:

```text
Initial
   ↓
Loading
   ↓
Success
   ↓
Error
   ↓
Empty
   ↓
Interaction
   ↓
Transition
```

O agente **não deverá implementar primeiro a funcionalidade e adicionar estados de Loading, Skeleton, Error, Success e Motion posteriormente como acabamento**.

Esses estados deverão ser considerados parte integrante da implementação.

---

# 14. Validação Antes da Entrega

Antes de concluir qualquer tarefa de frontend, o agente deverá verificar:

1. se a Skill **Motion Principles** foi consultada;
2. se os padrões da Skill foram aplicados;
3. se os estados de loading foram implementados quando necessários;
4. se Skeleton foi utilizado quando apropriado;
5. se Lazy Loading foi avaliado;
6. se entradas e saídas possuem transições adequadas;
7. se operações assíncronas possuem feedback;
8. se operações demoradas possuem progresso quando possível;
9. se `prefers-reduced-motion` é respeitado;
10. se a implementação não introduziu animações excessivas;
11. se performance e acessibilidade foram preservadas.

No Pull Request, registrar de forma objetiva quais aspectos de Motion Design foram implementados e quais foram considerados desnecessários, quando aplicável.

> **Princípio final:** toda interface deverá comunicar claramente ao usuário **o que está acontecendo, o que acabou de acontecer e o que acontecerá em seguida**, utilizando Skeleton, Loading States, Lazy Loading, transições, feedback e Motion Design de maneira funcional, acessível, consistente e performática — e não como mera decoração visual.

<br>

# Diretriz Obrigatória — Observabilidade, Qualidade de Código e Estratégia de Testes

O sistema deverá ser desenvolvido com uma estratégia completa de **observabilidade, qualidade de código, análise estática, testes automatizados e validação contínua**, desde o início do projeto.

Esses requisitos deverão ser tratados como **parte da arquitetura de engenharia do sistema**, e não como atividades opcionais ou melhorias posteriores.

> **Princípio fundamental:** toda funcionalidade nova ou alteração relevante deverá ser acompanhada dos testes, verificações de qualidade, instrumentação e evidências necessárias para garantir que o comportamento possa ser validado, monitorado e diagnosticado em produção.

Antes de adicionar qualquer ferramenta, o agente deverá:

1. inspecionar a stack tecnológica efetivamente utilizada pelo projeto;
2. verificar se já existe ferramenta equivalente configurada;
3. consultar `AGENTS.md`, `.agents/AGENTS.md` e demais políticas do repositório;
4. verificar configurações existentes de CI/CD;
5. evitar ferramentas redundantes ou incompatíveis;
6. preferir integração com ferramentas já existentes;
7. justificar tecnicamente qualquer nova dependência introduzida.

**Não instalar todas as ferramentas listadas indiscriminadamente.** A lista abaixo representa os recursos e categorias que deverão ser cobertos. Quando houver ferramentas equivalentes ou sobrepostas, deverá ser escolhida a combinação tecnicamente mais adequada à arquitetura do projeto.

---

# 1. Observabilidade

O sistema deverá possuir observabilidade suficiente para permitir identificar:

* erros;
* exceções;
* falhas de integração;
* degradação de performance;
* problemas de disponibilidade;
* comportamento das requisições;
* falhas em operações assíncronas;
* problemas de frontend;
* problemas de backend;
* dependências externas com falha;
* gargalos de performance.

A solução deverá considerar **OpenTelemetry** como camada de instrumentação e padronização quando compatível com a arquitetura.

Quando houver necessidade de plataforma de monitoramento, avaliar a utilização de soluções como:

* Sentry;
* Datadog;
* New Relic.

A escolha não deverá resultar em múltiplas plataformas redundantes sem justificativa técnica.

## 1.1 OpenTelemetry

Quando suportado pela stack, utilizar OpenTelemetry para instrumentação padronizada de:

* traces;
* métricas;
* logs;
* requisições HTTP;
* chamadas entre serviços;
* operações relevantes;
* dependências externas.

A instrumentação deverá permitir rastrear uma operação desde sua entrada até sua conclusão.

Exemplo conceitual:

```text
Usuário
   ↓
Frontend
   ↓
HTTP Request
   ↓
Backend
   ↓
Serviço
   ↓
Banco de Dados / API Externa
```

Quando tecnicamente possível, os eventos deverão possuir correlação suficiente para facilitar a investigação de uma mesma operação em diferentes camadas.

---

# 2. Error Tracking

O sistema deverá possuir mecanismo de captura e monitoramento de erros em produção.

A solução escolhida deverá permitir, conforme a stack:

* captura de exceções;
* stack traces;
* contexto da requisição;
* ambiente;
* versão/release;
* breadcrumbs;
* identificação de regressões;
* agrupamento de erros;
* acompanhamento da frequência;
* identificação de impacto.

Sentry, Datadog ou New Relic poderão ser utilizados conforme a arquitetura e as ferramentas já existentes.

Não registrar informações sensíveis desnecessariamente.

Nunca enviar para ferramentas de observabilidade:

* senhas;
* tokens;
* chaves privadas;
* secrets;
* credenciais;
* dados pessoais desnecessários;
* informações confidenciais.

---

# 3. Logs Estruturados

Quando houver logging de aplicação, preferir logs estruturados e adequados ao ambiente de execução.

Os logs deverão facilitar:

* pesquisa;
* filtragem;
* correlação;
* diagnóstico;
* análise de incidentes.

Quando apropriado, incluir informações como:

```text
timestamp
level
service
environment
request_id
trace_id
operation
status
duration
error
```

Não utilizar logs como substituição de métricas ou tracing quando estes forem mais apropriados.

---

# 4. Qualidade e Lint de Código

O projeto deverá possuir ferramentas automatizadas para impedir a introdução de código inconsistente, morto, inseguro ou desnecessariamente complexo.

A solução deverá avaliar, conforme a linguagem e stack:

* lint;
* formatação;
* análise estática;
* verificação de tipos;
* dependências não utilizadas;
* imports não utilizados;
* código morto;
* contratos;
* qualidade arquitetural;
* qualidade de commits.

Ferramentas como as seguintes deverão ser avaliadas quando compatíveis:

* Biome;
* Commitlint;
* Knip;
* Arch/ArchUnit ou mecanismo equivalente de testes arquiteturais;
* Stryker para mutation testing.

> **Importante:** ferramentas destinadas a ecossistemas diferentes não deverão ser instaladas apenas para cumprir uma lista. O agente deverá selecionar equivalentes adequados à linguagem efetivamente utilizada.

Por exemplo, se o projeto for Python, deverá utilizar as ferramentas de qualidade apropriadas ao ecossistema Python em vez de instalar ferramentas destinadas exclusivamente a JavaScript/TypeScript.

---

# 5. Contratos e Arquitetura

O sistema deverá possuir mecanismos que permitam verificar se os contratos e limites arquiteturais estão sendo respeitados.

Quando aplicável, implementar verificações automatizadas para impedir:

* dependências indevidas entre módulos;
* violações de camadas;
* imports proibidos;
* acoplamento arquitetural não autorizado;
* acesso direto a componentes que deveriam permanecer encapsulados.

As regras arquiteturais deverão ser executáveis e verificáveis automaticamente sempre que possível.

---

# 6. Commitlint e Qualidade do Git

Quando o projeto utilizar Git com commits padronizados, avaliar a adoção de **Commitlint** ou mecanismo equivalente adequado à stack.

Os commits deverão seguir a convenção definida pelo projeto.

Quando houver padrão estabelecido, preferir:

```text
feat:
fix:
refactor:
test:
docs:
chore:
ci:
perf:
```

Não alterar a convenção existente do projeto sem justificativa.

---

# 7. Detecção de Código e Dependências Não Utilizadas

Utilizar **Knip** ou ferramenta equivalente quando compatível com a stack para identificar:

* arquivos não utilizados;
* exports não utilizados;
* dependências não utilizadas;
* dependências redundantes;
* código potencialmente abandonado.

A ferramenta não deverá ser utilizada para remover automaticamente código sem análise.

Qualquer remoção deverá ser validada antes de ser aplicada.

---

# 8. Testes Unitários

Toda regra de negócio relevante deverá possuir testes unitários.

Os testes unitários deverão verificar principalmente:

* funções;
* classes;
* regras de negócio;
* validações;
* transformações;
* cálculos;
* tratamento de erros;
* casos extremos;
* regras de autorização;
* validações de configuração.

Os testes deverão ser:

* determinísticos;
* isolados;
* rápidos;
* reproduzíveis.

Não utilizar testes unitários como substituição dos testes de integração e E2E.

---

# 9. Testes de Integração

O sistema deverá possuir testes de integração para validar a comunicação entre componentes reais ou suficientemente representativos.

Avaliar, conforme a arquitetura:

* API ↔ banco de dados;
* backend ↔ serviços;
* autenticação;
* persistência;
* filas;
* cache;
* integrações externas;
* contratos de API;
* processamento de dados.

O objetivo é detectar problemas que não seriam encontrados exclusivamente por testes unitários.

---

# 10. Testes End-to-End

Fluxos críticos da aplicação deverão possuir testes **End-to-End (E2E)**.

Quando compatível com a stack frontend, utilizar **Playwright** ou solução equivalente.

Os testes E2E deverão validar fluxos reais do ponto de vista do usuário.

Exemplos:

```text
Acesso à aplicação
      ↓
Login
      ↓
Navegação
      ↓
Ação do usuário
      ↓
Processamento
      ↓
Resultado esperado
```

Priorizar E2E para:

* autenticação;
* navegação principal;
* fluxos críticos;
* criação de registros;
* edição;
* exclusão;
* pesquisas;
* filtros;
* operações financeiras, quando existentes;
* integrações críticas;
* fluxos de negócio essenciais.

Não transformar todos os testes em E2E. Utilizar a pirâmide de testes adequadamente.

---

# 11. Code Coverage

Quando compatível com a stack, utilizar **Codecov** ou solução equivalente para acompanhamento de cobertura de testes.

A cobertura deverá ser utilizada como **indicador de qualidade**, não como único critério de qualidade.

O agente deverá evitar:

* escrever testes artificiais apenas para aumentar percentual;
* excluir código relevante da cobertura sem justificativa;
* reduzir a cobertura para fazer o CI passar;
* utilizar cobertura como substituição de testes de comportamento.

Quando o projeto estabelecer um limite mínimo de cobertura, ele deverá ser respeitado no CI.

---

# 12. Mutation Testing

Quando tecnicamente viável, avaliar **Stryker** ou ferramenta equivalente de mutation testing adequada à linguagem.

O objetivo é verificar se os testes realmente detectam alterações no comportamento do código.

Exemplo conceitual:

```text
Código original
      ↓
Testes passam
      ↓
Mutation introduzida
      ↓
Testes devem falhar
```

Mutation testing deverá ser utilizado principalmente em:

* regras de negócio críticas;
* funções complexas;
* validações;
* componentes de alta criticidade.

Não executar mutation testing pesado obrigatoriamente em cada alteração local se isso causar impacto desproporcional no tempo de desenvolvimento.

Quando necessário, poderá ser executado em pipeline dedicado.

---

# 13. Pipeline de Qualidade

O CI/CD deverá executar automaticamente as verificações pertinentes.

Fluxo conceitual:

```text
Pull Request
      ↓
Install / Build
      ↓
Lint
      ↓
Type Check
      ↓
Static Analysis
      ↓
Unit Tests
      ↓
Integration Tests
      ↓
E2E
      ↓
Coverage
      ↓
Architecture / Contract Checks
      ↓
Quality Gate
      ↓
Deploy
```

A ordem exata deverá respeitar a arquitetura e o pipeline existente.

Falhas em verificações obrigatórias deverão impedir o avanço do pipeline quando isso estiver definido como política do projeto.

---

# 14. Pull Requests

Todo Pull Request deverá apresentar evidências reais das verificações executadas.

Quando aplicável, informar:

```text
Lint: PASS
Unit Tests: PASS
Integration Tests: PASS
E2E: PASS
Coverage: PASS
Architecture Checks: PASS
Contract Checks: PASS
Build: PASS
```

Não declarar uma verificação como aprovada sem execução efetiva.

Se uma verificação não puder ser executada, registrar:

* motivo;
* impacto;
* limitação;
* eventual ação necessária.

---

# 15. Observabilidade + Testes

A implementação deverá considerar conjuntamente **qualidade e observabilidade**.

Uma funcionalidade crítica deverá possuir, conforme aplicável:

```text
Código
 ├── Lint
 ├── Type Check
 ├── Static Analysis
 ├── Unit Tests
 ├── Integration Tests
 ├── E2E
 ├── Coverage
 ├── Contract/Architecture Tests
 └── Observability
       ├── Logs
       ├── Metrics
       ├── Traces
       └── Error Tracking
```

O objetivo é garantir que o sistema não apenas funcione durante o desenvolvimento, mas também possa ser **observado, diagnosticado e mantido após o deploy**.

---

# 16. Definition of Done — Qualidade e Observabilidade

Uma funcionalidade não deverá ser considerada concluída enquanto, quando aplicável:

* [ ] possuir testes unitários;
* [ ] possuir testes de integração;
* [ ] possuir testes E2E para fluxos críticos;
* [ ] passar pelo lint;
* [ ] passar pela formatação;
* [ ] passar pela análise estática;
* [ ] respeitar os contratos definidos;
* [ ] respeitar as regras arquiteturais;
* [ ] não introduzir código morto ou dependências desnecessárias;
* [ ] possuir cobertura adequada;
* [ ] não reduzir injustificadamente a cobertura existente;
* [ ] possuir instrumentação de observabilidade adequada;
* [ ] possuir tratamento e monitoramento de erros;
* [ ] possuir logs adequados quando necessários;
* [ ] possuir métricas/traces quando necessários;
* [ ] não expor informações sensíveis nos mecanismos de observabilidade;
* [ ] passar pelos Quality Gates definidos no CI/CD.

---

# 17. Regra Fundamental para o Agente

**Não instalar ou configurar ferramentas apenas porque foram mencionadas nesta diretriz.**

Antes de adicionar qualquer ferramenta, o agente deverá analisar:

1. linguagem utilizada;
2. framework;
3. frontend;
4. backend;
5. infraestrutura;
6. ferramentas já existentes;
7. CI/CD;
8. políticas do repositório;
9. sobreposição entre ferramentas;
10. custo de manutenção;
11. impacto no tempo de build;
12. compatibilidade com o projeto.

A solução final deverá buscar **cobertura completa das necessidades de qualidade, testes e observabilidade com o menor número razoável de ferramentas**.

> **Objetivo final:** o projeto deverá possuir uma engenharia verificável e contínua, na qual código de qualidade seja validado automaticamente, funcionalidades sejam protegidas por testes adequados, fluxos críticos sejam testados de ponta a ponta e o comportamento da aplicação possa ser observado e diagnosticado de forma confiável em produção.
