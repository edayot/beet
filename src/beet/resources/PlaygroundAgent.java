import java.lang.classfile.ClassFile;
import java.lang.classfile.ClassHierarchyResolver;
import java.lang.classfile.ClassTransform;
import java.lang.classfile.CodeBuilder;
import java.lang.classfile.CodeElement;
import java.lang.classfile.CodeTransform;
import java.lang.classfile.Instruction;
import java.lang.classfile.Opcode;
import java.lang.classfile.instruction.InvokeInstruction;
import java.lang.constant.ClassDesc;
import java.lang.constant.MethodTypeDesc;
import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.security.ProtectionDomain;
import java.util.ArrayList;
import java.util.List;

/**
 * Java agent loaded by the beet playground to fix the vanilla serverpack command.
 *
 * <p>The command builds the resource pack push packet with a null prompt instead of
 * an empty optional, which crashes the packet encoder and disconnects every player:
 *
 * <pre>new ClientboundResourcePackPushPacket(id, url, hash, false, null)</pre>
 *
 * <p>Like a mixin redirecting the argument, the agent rewrites the {@code aconst_null}
 * passed as the last {@code Optional} parameter of the constructor into a call to
 * {@code Optional.empty()}. The Class-File API takes care of offsets and stack maps.
 * When the bug is fixed upstream, the pattern is no longer found and nothing changes.
 */
public final class PlaygroundAgent implements ClassFileTransformer {
    private static final String TARGET = "net/minecraft/server/commands/ServerPackCommand";
    private static final ClassDesc PACKET =
            ClassDesc.ofInternalName("net/minecraft/network/protocol/common/ClientboundResourcePackPushPacket");
    private static final ClassDesc OPTIONAL = ClassDesc.of("java.util.Optional");

    public static void premain(String args, Instrumentation instrumentation) {
        instrumentation.addTransformer(new PlaygroundAgent());
    }

    @Override
    public byte[] transform(
            ClassLoader loader, String name, Class<?> redefined, ProtectionDomain domain, byte[] bytes) {
        if (!TARGET.equals(name)) {
            return null;
        }
        try {
            var resolver = ClassHierarchyResolver.defaultResolver();
            if (loader != null) {
                resolver = resolver.orElse(ClassHierarchyResolver.ofResourceParsing(loader));
            }
            var classFile = ClassFile.of(ClassFile.ClassHierarchyResolverOption.of(resolver));
            var transform = ClassTransform.transformingMethodBodies(CodeTransform.ofStateful(FixPrompt::new));
            return classFile.transformClass(classFile.parse(bytes), transform);
        } catch (Throwable error) {
            // Returning null keeps the original class
            System.out.println("[beet] Couldn't patch " + name + ": " + error);
            return null;
        }
    }

    /** Replace {@code aconst_null} right before the packet constructor by {@code Optional.empty()}. */
    private static final class FixPrompt implements CodeTransform {
        // Holds a pending aconst_null and the pseudo-instructions (labels, line numbers) after it
        private final List<CodeElement> pending = new ArrayList<>();

        @Override
        public void accept(CodeBuilder builder, CodeElement element) {
            if (!pending.isEmpty()) {
                if (!(element instanceof Instruction)) {
                    pending.add(element);
                    return;
                }
                if (isPromptConstructor(element)) {
                    builder.invokestatic(OPTIONAL, "empty", MethodTypeDesc.of(OPTIONAL));
                    pending.removeFirst();
                }
                flush(builder);
            }
            if (element instanceof Instruction instruction && instruction.opcode() == Opcode.ACONST_NULL) {
                pending.add(element);
            } else {
                builder.with(element);
            }
        }

        @Override
        public void atEnd(CodeBuilder builder) {
            flush(builder);
        }

        private void flush(CodeBuilder builder) {
            pending.forEach(builder::with);
            pending.clear();
        }

        private static boolean isPromptConstructor(CodeElement element) {
            if (!(element instanceof InvokeInstruction invoke)
                    || invoke.opcode() != Opcode.INVOKESPECIAL
                    || !invoke.owner().asSymbol().equals(PACKET)
                    || !invoke.name().equalsString("<init>")) {
                return false;
            }
            var parameters = invoke.typeSymbol().parameterList();
            return !parameters.isEmpty() && parameters.getLast().equals(OPTIONAL);
        }
    }
}
