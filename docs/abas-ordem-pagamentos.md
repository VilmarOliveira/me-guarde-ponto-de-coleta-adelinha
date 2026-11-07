# Abas e ordem dos pagamentos

As abas da Área do Ponto usam largura pelo ícone/texto, altura uniforme e quebra de linha em telas menores. A navegação superior de Configurações alinha-se à margem do conteúdo. Os cartões de textos de pagamento têm colunas de largura igual, reduzidas no tablet e celular.

Em **Pagamentos → Ordem e limites**, a tabela Ordem de exibição mantém Ordem, Forma de pagamento, Mínimo, Máximo e Ativo. Valores são alinhados à direita; os interruptores ficam centralizados no computador e verdes quando ativos. Até 800 px, as linhas tornam-se blocos rotulados; até 430 px, os valores usam uma coluna para manter R$ 100.000,00 legível.

Arraste pelos seis pontinhos com mouse ou toque; com a alça em foco, use **↑** e **↓**. Os botões Subir/Descer foram removidos. A numeração é atualizada ao mover; somente **Salvar alterações** grava. O foco e uma mensagem acessível acompanham a mudança. Sem JavaScript, o seletor de prioridade continua disponível.

Pagamento antecipado, Contratação e Renovação continuam independentes. Limites, ativação, título do botão e instruções mantêm os mesmos nomes de campos e validações do servidor. Reordenar move elementos existentes, preservando valores e interruptores. A primeira opção ativa e elegível continua sendo escolhida pela lógica existente; cobranças pendentes não são modificadas.

## Verificação

34 testes aprovados: ordem após salvar/recarregar, associação dos limites/textos à modalidade, isolamento das abas, prévia sem gravação, rejeição de prioridades duplicadas, permissões e regras de pagamentos. Chromium nas larguras 320, 360, 390, 430, 768, 1024 e 1440 px, incluindo margens, alturas das abas, valores completos, arraste real com mouse, Enter pelo teclado, toque, interruptores e persistência nas três abas. Sem erros JavaScript, rolagem horizontal ou cobranças reais. Emulação não substitui aparelhos físicos/Safari.

## Oracle

Sem novas variáveis, bibliotecas, migrações ou agendamentos. Após revisão e merge, atualize o checkout e reinicie `ponto-coleta.service` pelo procedimento habitual. As URLs de arquivos estáticos foram versionadas para atualizar o cache. Nenhum dado ou regra de cobrança foi regravado pela implementação; Oracle não foi acessada.
