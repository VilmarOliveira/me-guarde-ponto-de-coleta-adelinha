> Integração removida. As instruções de provedor deste documento são históricas e não devem ser executadas. Use [a remoção e os pagamentos locais](remocao-fast.md).

# Área do Cliente e pagamentos — novembro de 2026

## Uso

A Área do Cliente mantém `/cliente`, com seis abas: Visão geral, Meus pacotes, Meu plano, Histórico, Meus dados e Contratos e autorizações. No celular, o Menu da área indica a seleção. Dados e downloads são filtrados pelo cliente autenticado; links anteriores continuam disponíveis. Alterar senha exige a senha atual e invalida as outras sessões.

Na retirada, as modalidades são **Pix AI**, **Pix Fast** e **Dinheiro**. Pix AI usa exclusivamente a chave CNPJ e o titular já configurados, com QR Code local e conferência manual. Dinheiro preserva a confirmação manual. Nenhuma dessas modalidades consulta a FastFlow.

Pix Fast reserva os pacotes e fixa o orçamento calculado no servidor: pacotes + atraso → acréscimo → desconto → total. O desconto agora considera também o acréscimo. Pacotes cobertos pelo plano têm base zero; a taxa de atraso permanece separada. Pagamento confirmado não entrega pacotes nem consome créditos: a equipe ainda confere identidade/documento, recolhe assinatura e confirma a entrega física. O orçamento pago é preservado nessa confirmação.

Cobrança pendente impede alteração silenciosa de modalidade, valor ou pacotes e bloqueia retirada concorrente. Resolva ou cancele a cobrança antes de trocar de modalidade. Valores de Pix Fast devem estar entre R$ 4,00 e R$ 1.000,00; fora disso, use Pix AI ou Dinheiro. Total zero permite retirada sem chamada à API. Timeout não provoca repetição automática de criação; o pedido existente fica em consulta.

Ativação/renovação e cancelamento administrativos: [instruções](plano-validacao-administrador.md). Pagamentos de plano e retirada têm vínculos e validações distintos; uma cobrança não quita a outra. Situação `approved` isolada não confirma pagamento. Alertas de pagamento tardio, estorno ou duplicidade exigem análise da equipe e não alteram entregas ou documentos anteriores.

## Oracle

Não há novas variáveis, bibliotecas, credenciais ou timers. Preserve `/etc/ponto-coleta/site.env`, banco persistente, Gmail, Google e FastFlow. A configuração existente `FASTFLOW_PUBLIC_ORIGIN` determina a origem HTTPS dos callbacks; não é obtida do Host enviado pelo navegador.

O webhook global existente permanece. Cada cobrança informa automaticamente seu callback específico:

- Plano: `https://pontodecoletaadl.servehttp.com/webhooks/fastflow/pedidos/<UUID>`.
- Retirada: `https://pontodecoletaadl.servehttp.com/webhooks/fastflow/retiradas/<UUID>`.

Os UUIDs são criados pelo sistema; não cadastre literalmente `<UUID>`. Se houver restrição de caminhos no proxy/WAF, permita também o prefixo `/webhooks/fastflow/retiradas/`. A assinatura HMAC, os limites do corpo e a consulta de confirmação à API continuam obrigatórios. O job existente incorpora a reconciliação das retiradas, compartilhando o limite de consultas.

Após revisão e merge, na pasta real do serviço, com backup habitual do banco e uploads:

```bash
git status --short
git switch main
git pull --ff-only origin main
.venv/bin/python -m pip check
sudo systemctl restart ponto-coleta.service
sudo systemctl status ponto-coleta.service --no-pager
```

A inicialização acrescenta tabelas/colunas/índices idempotentemente. Execute a inicialização do schema sequencialmente antes de subir múltiplos workers. O script SQLite → PostgreSQL inclui as novas tabelas na ordem de suas referências. Registros, assinaturas, aceites, créditos e comprovantes anteriores não são regravados. Oracle não foi acessada ou reiniciada por esta implementação.

## Verificação

Suíte completa: **365 testes aprovados**, incluindo PostgreSQL 16 isolado e testes SQLite. Cobertura de permissões/isolamento, três modalidades, ordem de cálculo, total zero, limites, renovação antecipada, cancelamento incerto, webhook inválido, divergências, timeout, concorrência, entrega separada e pagamento tardio. API simulada e dados fictícios; nenhuma cobrança ou e-mail real.

Chromium: seis abas em 320, 360, 390, 430, 768, 1024 e 1440 px; sem rolagem horizontal ou erros JavaScript. Verificadas navegação móvel, QR Code, download PDF, assinatura preservada no redimensionamento, confirmação física, renovação manual e ativação Fast. Comando reproduzível em [tests/browser/README.md](../tests/browser/README.md).

Limitações: emulação não substitui aparelhos físicos/Safari. Falha HTTP real da Oracle e permissões efetivas da conta FastFlow dependem de homologação na instalação; não foram feitas chamadas financeiras reais.

Na entrada do pacote, peso e medidas determinam a categoria existente e consultam saldo/cobertura sem débito. A confirmação do recebimento mantém o débito transacional existente. Na retirada, a cobertura vem do lançamento registrado, mesmo com plano vencido ou contratado posteriormente. O limite R$ 4,00–R$ 1.000,00 aplica-se somente ao Pix Fast de retirada, após todos os ajustes; zero dispensa cobrança.

Verificação desta atualização: 139 testes aprovados nos módulos de aplicação, plano, administração e pagamentos; 3 cenários de PostgreSQL não executados nesta rodada. Limites com API simulada, sem cobranças reais.
