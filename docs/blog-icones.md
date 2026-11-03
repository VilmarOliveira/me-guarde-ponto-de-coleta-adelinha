# Blog com ícones e leitura contínua

## Preparar, colar e publicar

1. Abra **Área do Ponto → Configurações → Editor do blog** (`/painel/blog`). A edição/publicação conserva as permissões existentes.
2. Em um artigo novo, o modelo selecionado é **Texto com ícones — capa opcional**. Preencha título e introdução/resumo, depois cole o artigo completo no corpo.
3. Use as marcações no começo da linha, sem espaços antes do colchete:

   ```text
   Introdução completa do artigo.

   [icone:localizacao] Use nosso endereço

   Informe o endereço do Ponto de Coleta Adelinha na sua compra.

   [icone:identificacao] Informe seu nome e ID

   - Nome completo no destinatário.
   - ID do cadastro após o nome.

   [icone:sino] Aguarde o aviso de chegada

   Consulte [Dúvidas](/duvidas) quando precisar de atendimento.
   ```

4. A marcação cria um subtítulo H2 com ícone pequeno e decorativo. Texto, listas e parágrafos seguintes ocupam a largura da coluna; outro subtítulo começa a próxima seção. `##` cria H2 sem ícone; `###` cria um título menor sob uma seção existente. O título principal permanece H1.
5. Pesquise a biblioteca e escolha um ícone para inserir/trocar a marcação na linha do cursor. **Remover ícone da linha** mantém o título como subtítulo sem ícone. Textos já preparados com marcações não precisam de seleção manual.
6. Confira os contadores de caracteres com espaços do texto do editor e de palavras sem as marcações de ícones. Não há limite editorial obrigatório; o conteúdo completo é salvo em campo TEXT e exibido em uma página contínua, sem “ler mais” interno.
7. **Pré-visualizar** não salva nem publica: usa o mesmo renderizador e componente da página pública. A prévia fecha pelo botão, Esc ou área externa e devolve o foco ao botão de abertura. Avisos de marcações desconhecidas ajudam a corrigir o texto; publicamente essas linhas mantêm o subtítulo e omitem a marcação e o ícone inexistente.
8. Marque/desmarque **Mostrar índice clicável** conforme o artigo. Quando há subtítulos, ele aparece após a introdução. É recolhível, inclusive no celular. Destinos ordinais únicos evitam conflitos com títulos repetidos.
9. Salve o rascunho ou publique. Confira título/descrição para buscadores. O endereço dos artigos existentes é mantido na edição, e a publicação continua atualizando o texto sem exigir deploy.

Também são aceitos listas numeradas, **destaques com dois asteriscos**, blocos iniciados por `>`, links Markdown e URLs HTTP/HTTPS. HTML arbitrário é exibido como texto, sem execução; protocolos de script não se tornam links executáveis. Nenhuma API externa ou inteligência artificial interpreta o conteúdo.

## Identificadores iniciais

| Identificador | Assunto | Alternativa aceita |
| --- | --- | --- |
| `localizacao` | Localização e endereço | `endereco` |
| `identificacao` | Identificação e documento | `documento` |
| `usuario` | Usuário e cadastro | `cadastro` |
| `carrinho` | Carrinho e compras | `compras` |
| `pacote` | Pacote e armazenamento | `armazenamento` |
| `entrega` | Entrega e transporte | `transporte` |
| `sino` | Sino e aviso | `aviso` |
| `relogio` | Relógio e prazo | `prazo` |
| `calendario` | Calendário | — |
| `pagamento` | Pagamento e dinheiro | `dinheiro` |
| `seguranca` | Segurança | — |
| `duvidas` | Dúvidas e atendimento | `atendimento` |

Os SVGs estão no projeto (`static/blog-icons`), com desenhos distintos, traços arredondados, dimensões de 28 px no subtítulo e marcação decorativa para leitores de tela.

## Ícones personalizados

Somente administradores (e o acesso administrativo legado enquanto habilitado) podem enviar, substituir ou remover ícones da biblioteca. Um editor delegado mantém a edição/publicação e o uso dos ícones já disponíveis, sem obter essa permissão adicional.

Abra **Enviar ícone personalizado**, informe nome e identificador exclusivo e selecione um **PNG ou WebP de até 100 KB**. Recomendação: **128 × 128 px**, preferencialmente transparente. Outras proporções são aceitas e exibidas com `object-fit: contain`. O limite técnico de processamento é de 16 megapixels.

Confira a prévia antes de **Salvar ícone**. Para substituir, use o mesmo identificador; a biblioteca se atualiza sem apagar o artigo ainda não salvo. O navegador verifica tamanho, assinatura do arquivo e decodificação; o servidor verifica novamente o conteúdo real com Pillow. Nome/extensão/MIME informados pelo navegador não bastam. SVG, JPG, conteúdo inválido e excesso de tamanho são rejeitados sem substituir a imagem anterior.

**Remover da biblioteca** arquiva a entrada. Artigos que já usam o identificador continuam exibindo o ícone, inclusive na prévia e após reiniciar o servidor. O identificador pode ser reativado pelo administrador enviando novamente uma imagem com esse nome. As versões anteriores dos arquivos também continuam disponíveis: URLs contêm o hash do conteúdo e são servidas com ETag/cache imutável, para não quebrar páginas anteriormente carregadas.

## Acervo, persistência e implantação

Artigos existentes recebem apenas configurações aditivas: `layout='legacy'` e `toc_enabled=0`. Seus textos, slugs, metadados, datas e imagens não são regravados ou convertidos automaticamente. As capas já cadastradas são reaproveitadas nos dois modelos e aparecem acima do título e na listagem. Para mudar a formatação do artigo, o administrador escolhe explicitamente o modelo e salva. A capa só é substituída por um novo envio válido ou removida pela opção explícita de remoção. Consulte [Capa opcional no blog](blog-capa-opcional.md).

`blog_icon_assets` guarda os arquivos e seus tipos reais; `blog_icons` guarda nome, identificador, versão atual e estado na biblioteca. O banco SQLite/PostgreSQL persistente usado pela instalação conserva essas informações após reinicializações e atualizações. A inicialização cria as tabelas/colunas de modo aditivo. A ferramenta existente de migração SQLite → PostgreSQL também inclui os arquivos e o catálogo, na ordem das dependências.

Não há novas dependências, serviços pagos, credenciais ou variáveis Oracle. Após merge, siga o procedimento existente de backup, atualização do checkout e reinício do serviço. Não substitua o banco persistente por um arquivo vazio. A Oracle não foi acessada ou reiniciada nesta tarefa.

## Verificação

- **233 testes aprovados com SQLite**, incluindo artigos com mais de 8.000 e 50.000 caracteres, acentos, listas, links, marcações só no início da linha, títulos repetidos, índice opcional, equivalência prévia/publicação, metadados SEO, HTML malicioso e protocolos inseguros.
- **Dez testes aprovados em PostgreSQL isolado**, incluindo publicação, armazenamento dos ícones, arquivamento, persistência após recriar a aplicação e transferência real de SQLite para PostgreSQL.
- Chromium em **320, 360, 390, 430, 768, 1024 e 1440 px**: colar artigo longo, buscar/inserir/trocar/remover ícones, avisos, contadores, upload real PNG/WebP com prévia, rejeição de arquivo falso, substituição, arquivamento sem quebrar artigos, prévia com Esc, índice recolhível e links, texto contínuo, artigos sem capa e compatibilidade com capa antiga. Sem rolagem horizontal e sem execução do script usado como texto de teste.

Relatório/capturas: `/tmp/adelinha-blog-results`. As verificações de navegador usam emulação de computador/celular; não acessam a Oracle. Não havia imagens de referência dentro do texto enviado; a implementação segue as diretrizes de layout descritas nesse texto.
